from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from agentforge.activities import (
    CreateDeliveryApprovalInput,
    DeliverReportInput,
    DeliveryGateResult,
    ReadTicketsInput,
    ResolveDeliveryGateInput,
)
from agentforge.domain import (
    ApprovalDecision,
    ApprovalSignal,
    ApprovalSnapshot,
    ApprovalState,
    ArtifactRef,
    CancellationSignal,
    DeliveryReceipt,
    DraftRef,
    FailureKind,
    ProjectionEvent,
    RunState,
    SubmitRun,
    TicketSnapshotRef,
)
from agentforge.db_models import (
    AgentVersionRecord,
    ApprovalRecord,
    ArtifactRecord,
    DeliveryRecord,
    ProjectionEventRecord,
    RunRecord,
)
from agentforge.model_profiles import MOCK_MODEL_PROFILE
from agentforge.multi_agent import (
    SUPPORT_REPORT_GRAPH_V2,
    MultiAgentWorkflowInput,
    ResearchNoteRef,
    ReviewDecision,
    ReviewFeedbackRef,
    ReviewResult,
    TaskGraphLimits,
)
from agentforge.multi_agent_workflow import MultiAgentSupportReportWorkflow
from agentforge.role_activities import (
    CheckCancellationInput,
    PublishReviewedReportInput,
    ResearchActivityInput,
    ReviewReportActivityInput,
    WriteReportActivityInput,
)
from agentforge.repository import ProjectionRepository, RunRepository


SHA = "a" * 64


class GraphActivities:
    def __init__(
        self,
        *,
        review_decisions: tuple[ReviewDecision, ...] = (ReviewDecision.ACCEPT,),
        research_delays: dict[int, float] | None = None,
        block_research: bool = False,
        fail_research: bool = False,
        research_failure_delay: float = 0,
        block_publication: bool = False,
        post_publication_cancellation_delay: float = 0,
        delivery_failure: str | None = None,
        sessions: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.review_decisions = review_decisions
        self.research_delays = research_delays or {}
        self.block_research = block_research
        self.fail_research = fail_research
        self.research_failure_delay = research_failure_delay
        self.block_publication = block_publication
        self.post_publication_cancellation_delay = post_publication_cancellation_delay
        self.delivery_failure = delivery_failure
        self.sessions = sessions
        self.research_release = asyncio.Event()
        self.research_started = asyncio.Event()
        self.publication_release = asyncio.Event()
        self.publication_started = asyncio.Event()
        self.approval_created = asyncio.Event()
        self.cancelled = False
        self.events: list[str] = []
        self.writer_note_paths: list[tuple[str, ...]] = []
        self.states: list[RunState] = []
        self.projections: list[ProjectionEvent] = []
        self.research_attempts = 0
        self.active_research = 0
        self.max_active_research = 0
        self.approval: ApprovalSnapshot | None = None
        self.publish_count = 0
        self.delivery_count = 0

    @activity.defn(name="project_event")
    async def project_event(self, event: ProjectionEvent) -> bool:
        self.projections.append(event)
        self.states.append(event.run_state)
        if self.sessions is not None:
            async with self.sessions() as session:
                return await ProjectionRepository.apply(session, event)
        return True

    @activity.defn(name="read_tickets")
    async def read_tickets(self, input: ReadTicketsInput) -> TicketSnapshotRef:
        return TicketSnapshotRef(
            path=f"snapshots/{input.run_id}/tickets.json",
            sha256=SHA,
            size_bytes=1,
            ticket_count=len(input.ticket_files),
        )

    @activity.defn(name="check_cancellation")
    async def check_cancellation(self, input: CheckCancellationInput) -> bool:
        if self.publish_count and self.post_publication_cancellation_delay:
            await asyncio.sleep(self.post_publication_cancellation_delay)
        return self.cancelled

    @activity.defn(name="research_role")
    async def research_role(self, input: ResearchActivityInput) -> ResearchNoteRef:
        self.research_attempts += 1
        shard = input.shard_index
        self.events.append(f"start:{shard}")
        self.active_research += 1
        self.max_active_research = max(
            self.max_active_research, self.active_research
        )
        self.research_started.set()
        try:
            if self.block_research:
                await self.research_release.wait()
            delay = self.research_delays.get(shard, 0)
            if delay:
                await asyncio.sleep(delay)
            if self.fail_research:
                if self.research_failure_delay:
                    await asyncio.sleep(self.research_failure_delay)
                raise RuntimeError("retryable research failure")
            return ResearchNoteRef(
                path=f"intermediate/{input.run_id}/shard-{shard}/note.json",
                sha256=SHA,
                size_bytes=1,
            )
        finally:
            self.active_research -= 1
            self.events.append(f"finish:{shard}")

    @activity.defn(name="write_report_role")
    async def write_report_role(self, input: WriteReportActivityInput) -> DraftRef:
        self.events.append(f"write:{input.revision}")
        self.writer_note_paths.append(tuple(note.path for note in input.research_notes))
        return DraftRef(
            path=f"intermediate/{input.run_id}/draft-r{input.revision}.md",
            sha256=SHA,
            size_bytes=1,
        )

    @activity.defn(name="review_report_role")
    async def review_report_role(
        self, input: ReviewReportActivityInput
    ) -> ReviewResult:
        self.events.append(f"review:{input.review_round}")
        index = min(input.review_round, len(self.review_decisions) - 1)
        decision = self.review_decisions[index]
        if decision is ReviewDecision.ACCEPT:
            return ReviewResult(decision=decision)
        return ReviewResult(
            decision=decision,
            feedback=ReviewFeedbackRef(
                path=(
                    f"intermediate/{input.run_id}/review-{input.review_round}/"
                    "feedback.json"
                ),
                sha256=SHA,
                size_bytes=1,
            ),
        )

    @activity.defn(name="publish_reviewed_report")
    async def publish_reviewed_report(
        self, input: PublishReviewedReportInput
    ) -> ArtifactRef:
        self.publish_count += 1
        self.publication_started.set()
        if self.block_publication:
            await self.publication_release.wait()
        return ArtifactRef(
            path=f"reports/{input.run_id}/support-report.md",
            sha256=SHA,
            size_bytes=1,
            media_type="text/markdown",
        )

    @activity.defn(name="create_delivery_approval")
    async def create_delivery_approval(
        self, input: CreateDeliveryApprovalInput
    ) -> ApprovalSnapshot:
        now = datetime.now(UTC)
        self.approval = ApprovalSnapshot(
            approval_id=input.authorization.approval_id,
            run_id=input.run_id,
            operation_id=input.authorization.operation_id,
            action_hash=input.authorization.action_hash,
            action=input.authorization.action,
            state=ApprovalState.PENDING,
            expires_at=now + timedelta(seconds=30),
            created_at=now,
        )
        self.approval_created.set()
        return self.approval

    @activity.defn(name="resolve_delivery_gate")
    async def resolve_delivery_gate(
        self, input: ResolveDeliveryGateInput
    ) -> DeliveryGateResult:
        assert self.approval is not None
        return DeliveryGateResult(
            approval=self.approval,
            cancellation_requested=self.cancelled,
        )

    @activity.defn(name="deliver_report")
    async def deliver_report(self, input: DeliverReportInput) -> DeliveryReceipt:
        self.delivery_count += 1
        if self.delivery_failure == "uncertain":
            raise ApplicationError(
                "receiver result is uncertain",
                type="DeliveryUncertain",
                non_retryable=True,
            )
        if self.delivery_failure == "transport":
            raise RuntimeError("receiver transport failed")
        return DeliveryReceipt(
            delivery_id=uuid4(),
            operation_id=input.authorization.operation_id,
            artifact_sha256=input.authorization.action.artifact.sha256,
            committed_at=datetime.now(UTC),
        )

    @property
    def registered(self) -> list[object]:
        return [
            self.project_event,
            self.read_tickets,
            self.check_cancellation,
            self.research_role,
            self.write_report_role,
            self.review_report_role,
            self.publish_reviewed_report,
            self.create_delivery_approval,
            self.resolve_delivery_gate,
            self.deliver_report,
        ]


async def temporal_client() -> Client:
    return await Client.connect(
        "127.0.0.1:7233",
        namespace="agentforge-test",
        data_converter=pydantic_data_converter,
    )


def workflow_input(
    run_id: UUID,
    *,
    ticket_count: int = 4,
    limits: TaskGraphLimits = SUPPORT_REPORT_GRAPH_V2,
) -> MultiAgentWorkflowInput:
    return MultiAgentWorkflowInput(
        run_id=run_id,
        goal="Find recurring support themes",
        ticket_files=tuple(
            f"ticket-{index:03d}.txt" for index in range(1, ticket_count + 1)
        ),
        model_profile=MOCK_MODEL_PROFILE,
        limits=limits,
    )


async def start_graph(
    activities: GraphActivities,
    *,
    input: MultiAgentWorkflowInput | None = None,
) -> tuple[Worker, WorkflowHandle, MultiAgentWorkflowInput]:
    client = await temporal_client()
    actual_input = input or workflow_input(uuid4())
    queue = f"test-multi-agent-{uuid4()}"
    worker = Worker(
        client,
        task_queue=queue,
        workflows=[MultiAgentSupportReportWorkflow],
        activities=activities.registered,
    )
    await worker.__aenter__()
    handle = await client.start_workflow(
        MultiAgentSupportReportWorkflow.run,
        actual_input,
        id=f"test/multi-agent/{uuid4()}",
        task_queue=queue,
    )
    return worker, handle, actual_input


async def approve_exact(
    activities: GraphActivities, handle: WorkflowHandle
) -> ArtifactRef:
    await asyncio.wait_for(activities.approval_created.wait(), timeout=10)
    assert activities.approval is not None
    activities.approval = activities.approval.model_copy(
        update={"state": ApprovalState.APPROVED, "decided_at": datetime.now(UTC)}
    )
    await handle.signal(
        MultiAgentSupportReportWorkflow.approval_decided,
        ApprovalSignal(
            message_id=uuid4(),
            approval_id=activities.approval.approval_id,
            action_hash=activities.approval.action_hash,
            decision=ApprovalDecision.APPROVE,
        ),
    )
    return await handle.result()


@pytest.mark.integration
async def test_graph_runs_research_in_two_node_batches_and_preserves_ordinals() -> None:
    activities = GraphActivities(research_delays={0: 0.08, 1: 0.01, 2: 0.08, 3: 0.01})
    worker, handle, _ = await start_graph(activities)
    try:
        await approve_exact(activities, handle)
    finally:
        await worker.__aexit__(None, None, None)

    assert activities.max_active_research == 2
    assert activities.events.index("start:2") > activities.events.index("finish:0")
    assert activities.events.index("start:2") > activities.events.index("finish:1")
    assert activities.events.index("start:3") > activities.events.index("finish:0")
    assert activities.events.index("start:3") > activities.events.index("finish:1")


@pytest.mark.integration
async def test_out_of_order_research_completion_keeps_writer_note_order() -> None:
    activities = GraphActivities(research_delays={0: 0.08, 1: 0.01, 2: 0.08, 3: 0.01})
    worker, handle, _ = await start_graph(activities)
    try:
        await approve_exact(activities, handle)
    finally:
        await worker.__aexit__(None, None, None)

    assert [event for event in activities.events if event.startswith("finish:")] == [
        "finish:1",
        "finish:0",
        "finish:3",
        "finish:2",
    ]
    assert [
        f"/shard-{index}/" in f"/{path}"
        for index, path in enumerate(activities.writer_note_paths[0])
    ] == [True] * 4


@pytest.mark.integration
async def test_accept_path_publishes_then_waits_for_exact_human_approval() -> None:
    activities = GraphActivities()
    worker, handle, _ = await start_graph(activities)
    try:
        await asyncio.wait_for(activities.approval_created.wait(), timeout=10)
        assert activities.publish_count == 1
        assert activities.delivery_count == 0
        assert activities.approval is not None
        await handle.signal(
            MultiAgentSupportReportWorkflow.approval_decided,
            ApprovalSignal(
                message_id=uuid4(),
                approval_id=activities.approval.approval_id,
                action_hash="b" * 64,
                decision=ApprovalDecision.APPROVE,
            ),
        )
        await asyncio.sleep(0.1)
        assert activities.delivery_count == 0
        await approve_exact(activities, handle)
        history_json = (await handle.fetch_history()).to_json()
    finally:
        await worker.__aexit__(None, None, None)

    assert activities.delivery_count == 1
    for sentinel in (
        "PRIVATE_TICKET_BODY",
        "PRIVATE_RESEARCH_NOTE",
        "PRIVATE_DRAFT_BODY",
        "PRIVATE_REVIEW_FEEDBACK",
        "PRIVATE_MODEL_PROMPT",
        "PRIVATE_MODEL_RESPONSE",
    ):
        assert sentinel not in history_json


@pytest.mark.integration
async def test_two_revisions_are_allowed_and_third_rejection_exhausts_budget(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        version_id = (
            await session.scalars(
                select(AgentVersionRecord.id).where(
                    AgentVersionRecord.version == 2
                )
            )
        ).one()
    async with sessions() as session:
        submitted = await RunRepository.submit(
            session,
            SubmitRun(
                submission_key="revision-budget-durable",
                goal="Find recurring support themes",
                ticket_files=("ticket-001.txt", "ticket-002.txt"),
            ),
            version_id,
            workflow_kind="support-report-v2",
            graph_limits=SUPPORT_REPORT_GRAPH_V2,
        )
    activities = GraphActivities(
        review_decisions=(
            ReviewDecision.REVISE,
            ReviewDecision.REVISE,
            ReviewDecision.REVISE,
        ),
        sessions=sessions,
    )
    worker, handle, _ = await start_graph(
        activities,
        input=workflow_input(submitted.run.run_id, ticket_count=2),
    )
    try:
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    finally:
        await worker.__aexit__(None, None, None)

    assert [event for event in activities.events if event.startswith("write:")] == [
        "write:0",
        "write:1",
        "write:2",
    ]
    assert activities.states[-1] is RunState.BUDGET_EXHAUSTED
    assert activities.publish_count == 0
    async with sessions() as session:
        run = await session.get(RunRecord, submitted.run.run_id)
        projection_count = await session.scalar(
            select(func.count()).select_from(ProjectionEventRecord)
        )
        approval_count = await session.scalar(
            select(func.count()).select_from(ApprovalRecord)
        )
        delivery_count = await session.scalar(
            select(func.count()).select_from(DeliveryRecord)
        )
        artifact_count = await session.scalar(
            select(func.count()).select_from(ArtifactRecord)
        )
    assert run is not None and run.state == RunState.BUDGET_EXHAUSTED.value
    assert projection_count and projection_count > 0
    assert approval_count == 0
    assert delivery_count == 0
    assert artifact_count == 0


@pytest.mark.integration
async def test_ten_role_ceiling_refuses_an_eleventh_schedule() -> None:
    limits = SUPPORT_REPORT_GRAPH_V2.model_copy(update={"max_revisions": 3})
    activities = GraphActivities(review_decisions=(ReviewDecision.REVISE,))
    worker, handle, _ = await start_graph(
        activities,
        input=workflow_input(uuid4(), ticket_count=4, limits=limits),
    )
    try:
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    finally:
        await worker.__aexit__(None, None, None)

    role_events = [
        event
        for event in activities.events
        if event.startswith(("start:", "write:", "review:"))
    ]
    assert len(role_events) == 10
    assert "write:3" not in role_events
    assert activities.states[-1] is RunState.BUDGET_EXHAUSTED


@pytest.mark.integration
async def test_nine_hundred_second_deadline_ends_before_publication() -> None:
    limits = SUPPORT_REPORT_GRAPH_V2.model_copy(update={"max_elapsed_seconds": 1})
    activities = GraphActivities(research_delays={0: 1.2, 1: 1.2})
    worker, handle, _ = await start_graph(
        activities,
        input=workflow_input(uuid4(), ticket_count=2, limits=limits),
    )
    try:
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    finally:
        await worker.__aexit__(None, None, None)

    assert activities.states[-1] is RunState.BUDGET_EXHAUSTED
    assert activities.publish_count == 0


@pytest.mark.integration
async def test_graph_deadline_bounds_activity_retries_not_only_each_attempt() -> None:
    limits = SUPPORT_REPORT_GRAPH_V2.model_copy(update={"max_elapsed_seconds": 1})
    activities = GraphActivities(
        fail_research=True,
        research_failure_delay=0.45,
    )
    worker, handle, _ = await start_graph(
        activities,
        input=workflow_input(uuid4(), ticket_count=1, limits=limits),
    )
    started = asyncio.get_running_loop().time()
    try:
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    finally:
        await worker.__aexit__(None, None, None)

    elapsed = asyncio.get_running_loop().time() - started
    assert activities.research_attempts <= 2
    assert elapsed < 1.6
    assert activities.states[-1] is RunState.BUDGET_EXHAUSTED
    assert activities.publish_count == 0


@pytest.mark.integration
async def test_cancellation_during_batch_allows_batch_to_settle_but_schedules_nothing_later() -> None:
    activities = GraphActivities(block_research=True)
    worker, handle, input = await start_graph(activities)
    try:
        await asyncio.wait_for(activities.research_started.wait(), timeout=10)
        activities.cancelled = True
        await handle.signal(
            MultiAgentSupportReportWorkflow.cancellation_requested,
            CancellationSignal(
                message_id=uuid4(),
                request_id=uuid4(),
                run_id=input.run_id,
            ),
        )
        activities.research_release.set()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    finally:
        await worker.__aexit__(None, None, None)

    assert sorted(event for event in activities.events if event.startswith("finish:")) == [
        "finish:0",
        "finish:1",
    ]
    assert not any(event.startswith("write:") for event in activities.events)
    assert activities.states[-1] is RunState.CANCELLED
    assert activities.publish_count == 0


@pytest.mark.integration
async def test_cancellation_during_publication_creates_no_approval() -> None:
    activities = GraphActivities(block_publication=True)
    worker, handle, input = await start_graph(activities)
    try:
        await asyncio.wait_for(activities.publication_started.wait(), timeout=10)
        activities.cancelled = True
        await handle.signal(
            MultiAgentSupportReportWorkflow.cancellation_requested,
            CancellationSignal(
                message_id=uuid4(),
                request_id=uuid4(),
                run_id=input.run_id,
            ),
        )
        activities.publication_release.set()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    finally:
        await worker.__aexit__(None, None, None)

    assert activities.publish_count == 1
    assert activities.approval is None
    assert not activities.approval_created.is_set()
    assert activities.states[-1] is RunState.CANCELLED
    assert activities.delivery_count == 0


@pytest.mark.integration
async def test_graph_deadline_stops_after_successful_publication() -> None:
    limits = SUPPORT_REPORT_GRAPH_V2.model_copy(update={"max_elapsed_seconds": 1})
    activities = GraphActivities(post_publication_cancellation_delay=1.2)
    worker, handle, _ = await start_graph(
        activities,
        input=workflow_input(uuid4(), ticket_count=1, limits=limits),
    )
    try:
        artifact = await approve_exact(activities, handle)
    finally:
        await worker.__aexit__(None, None, None)

    assert artifact.sha256 == SHA
    assert activities.states[-1] is RunState.SUCCEEDED
    assert RunState.BUDGET_EXHAUSTED not in activities.states
    assert activities.delivery_count == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    ("delivery_failure", "expected_attempts"),
    (("uncertain", 1), ("transport", 3)),
)
async def test_delivery_failure_is_projected_as_uncertain(
    delivery_failure: str, expected_attempts: int
) -> None:
    activities = GraphActivities(delivery_failure=delivery_failure)
    worker, handle, _ = await start_graph(activities)
    try:
        await asyncio.wait_for(activities.approval_created.wait(), timeout=10)
        assert activities.approval is not None
        activities.approval = activities.approval.model_copy(
            update={"state": ApprovalState.APPROVED, "decided_at": datetime.now(UTC)}
        )
        await handle.signal(
            MultiAgentSupportReportWorkflow.approval_decided,
            ApprovalSignal(
                message_id=uuid4(),
                approval_id=activities.approval.approval_id,
                action_hash=activities.approval.action_hash,
                decision=ApprovalDecision.APPROVE,
            ),
        )
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    finally:
        await worker.__aexit__(None, None, None)

    assert activities.delivery_count == expected_attempts
    assert activities.projections[-1].run_state is RunState.NEEDS_ATTENTION
    assert activities.projections[-1].failure_kind is FailureKind.UNCERTAIN
    assert activities.projections[-1].artifact is not None
