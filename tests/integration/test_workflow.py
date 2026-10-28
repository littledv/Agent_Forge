from __future__ import annotations

import asyncio
import hashlib
import socket
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from temporalio.client import Client, WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
import uvicorn

from agentforge.config import InboxSettings, Settings
from agentforge.db_models import (
    ApprovalRecord,
    ArtifactRecord,
    AttemptRecord,
    DeliveryRecord,
    RunRecord,
    StepRecord,
)
from agentforge.domain import (
    AgentAction,
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalSignal,
    AttemptState,
    CancellationSignal,
    DeliveryReceipt,
    DeliveryRequest,
    FailureKind,
    ModelDecisionInput,
    ModelProfileSnapshot,
    ProjectionEvent,
    ReadTicketsAction,
    RunState,
    StepState,
    SubmitRun,
    WorkflowInput,
)
from agentforge.mock_model import DeterministicMockModel
from agentforge.inbox_api import create_inbox_app
from agentforge.inbox_db_models import InboxDeliveryRecord
from agentforge.model_profiles import MOCK_MODEL_PROFILE, build_ollama_profile
from agentforge.ollama_model import (
    ModelBudgetExceeded,
    ModelConfigurationError,
    ModelOutputInvalid,
    ModelRequestRejected,
    ModelTransportError,
)
from agentforge.repository import (
    ApprovalRepository,
    CancellationRepository,
    ProjectionRepository,
    RunRepository,
)
from agentforge.storage import ArtifactStorage, StorageViolation
from agentforge.worker import create_worker
from agentforge.workflow import SupportReportWorkflow


def workflow_settings(
    database_url: str,
    tmp_path: Path,
    *,
    max_decisions: int = 4,
    approval_ttl_seconds: int = 30,
    inbox_base_url: str = "http://127.0.0.1:8001",
) -> Settings:
    tickets = tmp_path / "tickets"
    tickets.mkdir(exist_ok=True)
    (tickets / "ticket-001.txt").write_text(
        "Export remains pending and no download appears.\n", encoding="utf-8"
    )
    (tickets / "ticket-002.txt").write_text(
        "Password reset instructions are hard to find.\n", encoding="utf-8"
    )
    return Settings(
        database_url=database_url,
        temporal_address="127.0.0.1:7233",
        temporal_namespace="agentforge-test",
        temporal_task_queue="agentforge-support-report-v1",
        ticket_root=tickets,
        artifact_root=tmp_path / "artifacts",
        max_decisions=max_decisions,
        max_ticket_bytes=65_536,
        max_total_ticket_bytes=262_144,
        approval_ttl_seconds=approval_ttl_seconds,
        inbox_base_url=inbox_base_url,
    )


def test_workflow_settings_use_isolated_temporal_namespace(tmp_path: Path) -> None:
    settings = workflow_settings("postgresql+psycopg://test", tmp_path)

    assert settings.temporal_namespace == "agentforge-test"


async def submit_run(
    engine: AsyncEngine,
    *,
    key: str,
    ticket_files: tuple[str, ...] = ("ticket-001.txt", "ticket-002.txt"),
    model_profile: ModelProfileSnapshot = MOCK_MODEL_PROFILE,
) -> tuple[UUID, str]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    from agentforge.db_models import AgentVersionRecord

    async with sessions() as session:
        version_id = (
            await session.scalars(
                select(AgentVersionRecord.id).where(
                    AgentVersionRecord.version == 1
                )
            )
        ).one()
    async with sessions() as session:
        result = await RunRepository.submit(
            session,
            SubmitRun(
                submission_key=key,
                goal="Find recurring support themes",
                ticket_files=ticket_files,
                model_profile_id=model_profile.profile_id,
            ),
            version_id,
            model_profile=model_profile,
        )
    return result.run.run_id, result.run.workflow_id


async def temporal_client(settings: Settings) -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )


@asynccontextmanager
async def running_http_app(app: FastAPI) -> AsyncIterator[str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(160):
            if server.started:
                break
            if task.done():
                await task
                pytest.fail("loopback inbox exited before readiness")
            await asyncio.sleep(0.05)
        else:
            pytest.fail("loopback inbox did not become ready")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=8)
        listener.close()


class RecordingInboxClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[DeliveryRequest] = []

    def deliver(self, request: DeliveryRequest) -> DeliveryReceipt:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return DeliveryReceipt(
            delivery_id=uuid4(),
            operation_id=request.operation_id,
            artifact_sha256=request.artifact.sha256,
            committed_at=datetime.now(UTC),
        )


class BlockingInboxClient(RecordingInboxClient):
    def __init__(self) -> None:
        super().__init__()
        self.committed = threading.Event()
        self.release = threading.Event()

    def deliver(self, request: DeliveryRequest) -> DeliveryReceipt:
        receipt = super().deliver(request)
        self.committed.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test did not release committed delivery")
        return receipt


async def wait_for_approval(
    engine: AsyncEngine, run_id: UUID, *, timeout: float = 10
) -> ApprovalRecord:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        async with sessions() as session:
            approval = await session.scalar(
                select(ApprovalRecord).where(ApprovalRecord.run_id == run_id)
            )
        if approval is not None:
            return approval
        await asyncio.sleep(0.05)
    pytest.fail("workflow did not create an approval")


async def decide_approval(
    engine: AsyncEngine, approval: ApprovalRecord, decision: ApprovalDecision
) -> ApprovalSignal:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        await ApprovalRepository.decide(
            session,
            approval.id,
            ApprovalDecisionRequest(
                action_hash=approval.action_hash,
                decision=decision,
            ),
        )
    return ApprovalSignal(
        message_id=uuid5(approval.id, f"decision:{decision.value}"),
        approval_id=approval.id,
        action_hash=approval.action_hash,
        decision=decision,
    )


def activity_count(history: dict[str, object], name: str) -> int:
    count = 0
    for event in history["events"]:
        scheduled = event.get("activityTaskScheduledEventAttributes")
        if scheduled and scheduled["activityType"]["name"] == name:
            count += 1
    return count


def activity_timeouts(history: dict[str, object], name: str) -> set[str]:
    timeouts = set()
    for event in history["events"]:
        scheduled = event.get("activityTaskScheduledEventAttributes")
        if scheduled and scheduled["activityType"]["name"] == name:
            timeouts.add(scheduled["startToCloseTimeout"])
    return timeouts


@pytest.mark.integration
async def test_real_workflow_waits_for_matching_approval_then_delivers_once(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    settings = workflow_settings(test_database_url, tmp_path)
    run_id, workflow_id = await submit_run(database_engine, key="workflow-happy")
    inbox = RecordingInboxClient()
    worker = await create_worker(settings, inbox_client=inbox)
    client = await temporal_client(settings)
    workflow_input = WorkflowInput(
        run_id=run_id,
        goal="Find recurring support themes",
        ticket_files=("ticket-001.txt", "ticket-002.txt"),
        max_decisions=4,
    )

    async with worker:
        handle = await client.start_workflow(
            SupportReportWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        approval = await wait_for_approval(database_engine, run_id)
        assert inbox.requests == []

        wrong = ApprovalSignal(
            message_id=uuid4(),
            approval_id=approval.id,
            action_hash="f" * 64,
            decision=ApprovalDecision.APPROVE,
        )
        await handle.signal(SupportReportWorkflow.approval_decided, wrong)
        await asyncio.sleep(0.1)
        assert inbox.requests == []

        approved = await decide_approval(
            database_engine, approval, ApprovalDecision.APPROVE
        )
        await handle.signal(SupportReportWorkflow.approval_decided, approved)
        await handle.signal(SupportReportWorkflow.approval_decided, approved)
        artifact = await handle.result()

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
        artifact_count = await session.scalar(
            select(func.count()).select_from(ArtifactRecord)
        )
        delivery_count = await session.scalar(
            select(func.count()).select_from(DeliveryRecord)
        )
    report_bytes = (settings.artifact_root / artifact.path).read_bytes()
    history = await client.get_workflow_handle(workflow_id).fetch_history()
    history_json = history.to_json()

    assert run is not None and run.state == RunState.SUCCEEDED.value
    assert artifact_count == 1
    assert delivery_count == 1
    assert len(inbox.requests) == 1
    assert artifact.sha256 == hashlib.sha256(report_bytes).hexdigest()
    assert "Export remains pending" not in history_json
    assert "# Support Report" not in history_json
    history_dict = history.to_json_dict()
    assert activity_count(history_dict, "decide_action") <= 4
    assert activity_timeouts(history_dict, "decide_action") == {"180s"}
    assert activity_timeouts(history_dict, "read_tickets") == {"10s"}


@pytest.mark.integration
@pytest.mark.parametrize("outcome", ["rejected", "expired"])
async def test_rejection_or_expiry_denies_delivery(
    database_engine: AsyncEngine,
    inbox_database_engine: AsyncEngine,
    test_database_url: str,
    inbox_test_database_url: str,
    tmp_path: Path,
    outcome: str,
) -> None:
    inbox_app = create_inbox_app(
        InboxSettings(database_url=inbox_test_database_url)
    )
    async with running_http_app(inbox_app) as inbox_base_url:
        settings = workflow_settings(
            test_database_url,
            tmp_path,
            approval_ttl_seconds=1 if outcome == "expired" else 30,
            inbox_base_url=inbox_base_url,
        )
        run_id, workflow_id = await submit_run(
            database_engine, key=f"workflow-{outcome}"
        )
        worker = await create_worker(settings)
        client = await temporal_client(settings)

        async with worker:
            handle = await client.start_workflow(
                SupportReportWorkflow.run,
                WorkflowInput(
                    run_id=run_id,
                    goal="Find recurring support themes",
                    ticket_files=("ticket-001.txt", "ticket-002.txt"),
                    max_decisions=4,
                ),
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
            )
            approval = await wait_for_approval(database_engine, run_id)
            if outcome == "rejected":
                rejected = await decide_approval(
                    database_engine, approval, ApprovalDecision.REJECT
                )
                await handle.signal(
                    SupportReportWorkflow.approval_decided, rejected
                )
            with pytest.raises(WorkflowFailureError):
                await handle.result()

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
        delivery_count = await session.scalar(
            select(func.count()).select_from(DeliveryRecord)
        )
    inbox_sessions = async_sessionmaker(
        inbox_database_engine, expire_on_commit=False
    )
    async with inbox_sessions() as session:
        inbox_count = await session.scalar(
            select(func.count()).select_from(InboxDeliveryRecord)
        )
    assert run is not None and run.state == RunState.DENIED.value
    assert delivery_count == 0
    assert inbox_count == 0


@pytest.mark.integration
async def test_persisted_cancellation_wins_before_delivery_without_its_signal(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    settings = workflow_settings(test_database_url, tmp_path)
    run_id, workflow_id = await submit_run(database_engine, key="workflow-cancel")
    inbox = RecordingInboxClient()
    worker = await create_worker(settings, inbox_client=inbox)
    client = await temporal_client(settings)
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)

    async with worker:
        handle = await client.start_workflow(
            SupportReportWorkflow.run,
            WorkflowInput(
                run_id=run_id,
                goal="Find recurring support themes",
                ticket_files=("ticket-001.txt", "ticket-002.txt"),
                max_decisions=4,
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        approval = await wait_for_approval(database_engine, run_id)
        async with sessions() as session:
            await CancellationRepository.request(session, run_id)
        approved = await decide_approval(
            database_engine, approval, ApprovalDecision.APPROVE
        )
        await handle.signal(SupportReportWorkflow.approval_decided, approved)
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
    assert run is not None and run.state == RunState.CANCELLED.value
    assert inbox.requests == []


@pytest.mark.integration
async def test_cancellation_during_running_projection_prevents_delivery(
    database_engine: AsyncEngine,
    test_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = workflow_settings(test_database_url, tmp_path)
    run_id, workflow_id = await submit_run(
        database_engine, key="workflow-cancel-during-running-projection"
    )
    inbox = RecordingInboxClient()
    projection_started = asyncio.Event()
    release_projection = asyncio.Event()
    original_apply = ProjectionRepository.apply

    async def blocking_apply(session, event: ProjectionEvent) -> bool:
        if event.run_id == run_id and event.sequence == 3:
            projection_started.set()
            await release_projection.wait()
        return await original_apply(session, event)

    monkeypatch.setattr(ProjectionRepository, "apply", blocking_apply)
    worker = await create_worker(settings, inbox_client=inbox)
    client = await temporal_client(settings)
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)

    async with worker:
        handle = await client.start_workflow(
            SupportReportWorkflow.run,
            WorkflowInput(
                run_id=run_id,
                goal="Find recurring support themes",
                ticket_files=("ticket-001.txt", "ticket-002.txt"),
                max_decisions=4,
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        approval = await wait_for_approval(database_engine, run_id)
        approved = await decide_approval(
            database_engine, approval, ApprovalDecision.APPROVE
        )
        await handle.signal(SupportReportWorkflow.approval_decided, approved)
        await asyncio.wait_for(projection_started.wait(), timeout=10)
        async with sessions() as session:
            cancellation = await CancellationRepository.request(session, run_id)
        await handle.signal(
            SupportReportWorkflow.cancellation_requested,
            CancellationSignal(
                message_id=uuid5(cancellation.cancellation.request_id, "signal"),
                request_id=cancellation.cancellation.request_id,
                run_id=run_id,
            ),
        )
        release_projection.set()
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
    assert run is not None and run.state == RunState.CANCELLED.value
    assert inbox.requests == []


@pytest.mark.integration
async def test_cancellation_during_final_model_activity_prevents_success(
    database_engine: AsyncEngine,
    test_database_url: str,
    tmp_path: Path,
) -> None:
    settings = workflow_settings(test_database_url, tmp_path)
    run_id, workflow_id = await submit_run(
        database_engine, key="workflow-cancel-during-final-model"
    )
    inbox = RecordingInboxClient()
    final_decision_started = threading.Event()
    cancellation_written = threading.Event()
    thread_errors: list[BaseException] = []
    storage = ArtifactStorage(
        ticket_root=settings.ticket_root,
        artifact_root=settings.artifact_root,
        max_ticket_bytes=settings.max_ticket_bytes,
        max_total_ticket_bytes=settings.max_total_ticket_bytes,
    )
    delegate = DeterministicMockModel(
        storage, max_draft_bytes=settings.max_total_ticket_bytes
    )

    class BlockingFinalModel:
        async def decide(
            self, input: ModelDecisionInput, usage: object
        ) -> AgentAction:
            if input.run_id == run_id and input.decision_number == 4:
                final_decision_started.set()
                if not await asyncio.to_thread(cancellation_written.wait, 10):
                    raise TimeoutError("test did not persist final cancellation")
            return await delegate.decide(input, usage)  # type: ignore[arg-type]

    def persist_cancellation() -> None:
        try:
            if not final_decision_started.wait(timeout=10):
                raise TimeoutError("workflow did not start the final decision")

            async def persist() -> None:
                engine = create_async_engine(test_database_url)
                sessions = async_sessionmaker(engine, expire_on_commit=False)
                try:
                    async with sessions() as session:
                        await CancellationRepository.request(session, run_id)
                finally:
                    await engine.dispose()

            asyncio.run(persist(), loop_factory=asyncio.SelectorEventLoop)
        except BaseException as error:
            thread_errors.append(error)
        finally:
            cancellation_written.set()

    cancellation_thread = threading.Thread(target=persist_cancellation, daemon=True)
    cancellation_thread.start()
    worker = await create_worker(
        settings, storage=storage, model=BlockingFinalModel(), inbox_client=inbox
    )
    client = await temporal_client(settings)
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)

    async with worker:
        handle = await client.start_workflow(
            SupportReportWorkflow.run,
            WorkflowInput(
                run_id=run_id,
                goal="Find recurring support themes",
                ticket_files=("ticket-001.txt", "ticket-002.txt"),
                max_decisions=4,
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        approval = await wait_for_approval(database_engine, run_id)
        approved = await decide_approval(
            database_engine, approval, ApprovalDecision.APPROVE
        )
        await handle.signal(SupportReportWorkflow.approval_decided, approved)
        assert await asyncio.to_thread(cancellation_written.wait, 10)
        assert thread_errors == []
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    cancellation_thread.join(timeout=2)
    assert not cancellation_thread.is_alive()
    assert thread_errors == []

    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
    assert run is not None and run.state == RunState.CANCELLED.value
    assert len(inbox.requests) == 1


@pytest.mark.integration
async def test_retry_exhaustion_projects_uncertain_delivery(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    from agentforge.inbox_client import InboxRetryableError

    settings = workflow_settings(test_database_url, tmp_path)
    run_id, workflow_id = await submit_run(database_engine, key="workflow-uncertain")
    inbox = RecordingInboxClient(InboxRetryableError("receiver unavailable"))
    worker = await create_worker(settings, inbox_client=inbox)
    client = await temporal_client(settings)

    async with worker:
        handle = await client.start_workflow(
            SupportReportWorkflow.run,
            WorkflowInput(
                run_id=run_id,
                goal="Find recurring support themes",
                ticket_files=("ticket-001.txt", "ticket-002.txt"),
                max_decisions=4,
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        approval = await wait_for_approval(database_engine, run_id)
        approved = await decide_approval(
            database_engine, approval, ApprovalDecision.APPROVE
        )
        await handle.signal(SupportReportWorkflow.approval_decided, approved)
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
        delivery_step = await session.scalar(
            select(StepRecord).where(StepRecord.name == "deliver_report")
        )
    assert run is not None and run.state == RunState.NEEDS_ATTENTION.value
    assert delivery_step is not None
    assert delivery_step.state == StepState.UNCERTAIN.value
    assert len(inbox.requests) == 3


@pytest.mark.integration
async def test_cancellation_after_receiver_commit_keeps_receipt_and_stops_work(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    settings = workflow_settings(test_database_url, tmp_path)
    run_id, workflow_id = await submit_run(
        database_engine, key="workflow-cancel-after-commit"
    )
    inbox = BlockingInboxClient()
    worker = await create_worker(settings, inbox_client=inbox)
    client = await temporal_client(settings)
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)

    async with worker:
        handle = await client.start_workflow(
            SupportReportWorkflow.run,
            WorkflowInput(
                run_id=run_id,
                goal="Find recurring support themes",
                ticket_files=("ticket-001.txt", "ticket-002.txt"),
                max_decisions=4,
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        approval = await wait_for_approval(database_engine, run_id)
        approved = await decide_approval(
            database_engine, approval, ApprovalDecision.APPROVE
        )
        await handle.signal(SupportReportWorkflow.approval_decided, approved)
        assert await asyncio.to_thread(inbox.committed.wait, 10)
        async with sessions() as session:
            cancellation = await CancellationRepository.request(session, run_id)
        await handle.signal(
            SupportReportWorkflow.cancellation_requested,
            CancellationSignal(
                message_id=uuid5(cancellation.cancellation.request_id, "signal"),
                request_id=cancellation.cancellation.request_id,
                run_id=run_id,
            ),
        )
        inbox.release.set()
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
        delivery_count = await session.scalar(
            select(func.count()).select_from(DeliveryRecord)
        )
    history = await client.get_workflow_handle(workflow_id).fetch_history()
    assert run is not None and run.state == RunState.CANCELLED.value
    assert delivery_count == 1
    assert len(inbox.requests) == 1
    assert activity_count(history.to_json_dict(), "decide_action") == 3


class NeverFinishModel:
    async def decide(
        self, input: ModelDecisionInput, usage: object
    ) -> ReadTicketsAction:
        return ReadTicketsAction(type="read_tickets", ticket_files=input.ticket_files)


class InvalidOutputModel:
    async def decide(self, input: ModelDecisionInput, usage: object) -> object:
        return {"type": "invented_action", "secret": "must-not-run"}


class UnauthorizedReadModel:
    async def decide(
        self, input: ModelDecisionInput, usage: object
    ) -> ReadTicketsAction:
        return ReadTicketsAction(type="read_tickets", ticket_files=("ticket-002.txt",))


class FailingDecisionModel:
    def __init__(self, error_type: type[Exception]) -> None:
        self._error_type = error_type

    async def decide(self, input: ModelDecisionInput, usage: object) -> AgentAction:
        if self._error_type is ModelOutputInvalid:
            raise ModelOutputInvalid("repair_exhausted")
        raise self._error_type("PRIVATE-MODEL-DETAIL")


class UsageThenBudgetModel:
    async def decide(self, input: ModelDecisionInput, usage: object) -> AgentAction:
        await usage.request_started(estimated_input_tokens=25)  # type: ignore[attr-defined]
        await usage.response_completed(  # type: ignore[attr-defined]
            estimated_input_tokens=25,
            input_tokens=20,
            output_tokens=5,
            duration_ms=120,
        )
        if input.decision_number == 1:
            return ReadTicketsAction(
                type="read_tickets", ticket_files=input.ticket_files
            )
        raise ModelBudgetExceeded("PRIVATE-MODEL-DETAIL")


@pytest.mark.integration
async def test_ollama_activity_usage_is_persisted_without_content(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    profile = build_ollama_profile(
        model_name="qwen3:4b-instruct-2507-q4_K_M",
        model_digest="a" * 64,
        context_tokens=8_192,
        max_output_tokens=2_048,
        max_prompt_chars=24_000,
        max_report_chars=16_384,
        max_repairs=2,
    )
    base_settings = workflow_settings(test_database_url, tmp_path)
    settings = Settings.model_validate(
        {
            **base_settings.model_dump(),
            "enabled_model_profiles": (
                "mock-v1",
                "ollama-qwen3-4b-instruct-q4-v1",
            ),
            "worker_model_profile": "ollama-qwen3-4b-instruct-q4-v1",
            "ollama_model_digest": "a" * 64,
        }
    )
    run_id, workflow_id = await submit_run(
        database_engine, key="workflow-model-usage", model_profile=profile
    )
    worker = await create_worker(settings, model=UsageThenBudgetModel())
    client = await temporal_client(settings)

    async with worker:
        with pytest.raises(WorkflowFailureError):
            await client.execute_workflow(
                SupportReportWorkflow.run,
                WorkflowInput(
                    run_id=run_id,
                    goal="Find recurring support themes",
                    ticket_files=("ticket-001.txt", "ticket-002.txt"),
                    max_decisions=4,
                    model_profile=profile,
                ),
                id=workflow_id,
                task_queue=profile.task_queue,
            )

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
        steps = list(
            (
                await session.scalars(
                    select(StepRecord).where(StepRecord.run_id == run_id)
                )
            ).all()
        )
        attempts = list((await session.scalars(select(AttemptRecord))).all())
    step_names = {step.id: step.name for step in steps}
    model_attempts = [
        attempt
        for attempt in attempts
        if step_names[attempt.step_id] == "decide_action"
    ]
    tool_attempts = [
        attempt for attempt in attempts if step_names[attempt.step_id] == "read_tickets"
    ]
    history = await client.get_workflow_handle(workflow_id).fetch_history()

    assert run is not None and run.state == RunState.BUDGET_EXHAUSTED.value
    assert len(model_attempts) == 2
    assert all(attempt.model_provider == "ollama" for attempt in model_attempts)
    assert all(attempt.model_request_count == 1 for attempt in model_attempts)
    assert all(attempt.model_input_tokens == 20 for attempt in model_attempts)
    assert all(attempt.model_output_tokens == 5 for attempt in model_attempts)
    assert all(attempt.model_duration_ms == 120 for attempt in model_attempts)
    assert all(attempt.provider_cost_estimate_usd == 0 for attempt in model_attempts)
    assert all(attempt.input_tokens_estimated is False for attempt in model_attempts)
    assert all(attempt.usage_uncertain is False for attempt in model_attempts)
    assert len(tool_attempts) == 1 and tool_attempts[0].model_provider is None
    assert "PRIVATE-MODEL-DETAIL" not in history.to_json()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error_type", "expected_state", "expected_failure", "attempt_count"),
    [
        (ModelTransportError, RunState.FAILED, FailureKind.RETRYABLE, 3),
        (ModelRequestRejected, RunState.FAILED, FailureKind.PERMANENT, 1),
        (ModelConfigurationError, RunState.FAILED, FailureKind.PERMANENT, 1),
        (ModelOutputInvalid, RunState.FAILED, FailureKind.PERMANENT, 1),
        (StorageViolation, RunState.FAILED, FailureKind.PERMANENT, 1),
        (ModelBudgetExceeded, RunState.BUDGET_EXHAUSTED, None, 1),
    ],
)
async def test_model_failure_classification_is_durable_and_sanitized(
    database_engine: AsyncEngine,
    test_database_url: str,
    tmp_path: Path,
    error_type: type[Exception],
    expected_state: RunState,
    expected_failure: FailureKind | None,
    attempt_count: int,
) -> None:
    settings = workflow_settings(test_database_url, tmp_path)
    run_id, workflow_id = await submit_run(
        database_engine, key=f"model-failure-{error_type.__name__}"
    )
    worker = await create_worker(settings, model=FailingDecisionModel(error_type))
    client = await temporal_client(settings)

    async with worker:
        with pytest.raises(WorkflowFailureError):
            await client.execute_workflow(
                SupportReportWorkflow.run,
                WorkflowInput(
                    run_id=run_id,
                    goal="Find recurring support themes",
                    ticket_files=("ticket-001.txt", "ticket-002.txt"),
                    max_decisions=4,
                ),
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
            )

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
        attempts = list((await session.scalars(select(AttemptRecord))).all())
    history = await client.get_workflow_handle(workflow_id).fetch_history()

    assert run is not None and run.state == expected_state.value
    assert run.failure_kind == (
        expected_failure.value if expected_failure is not None else None
    )
    assert len(attempts) == attempt_count
    assert all(attempt.state == AttemptState.FAILED.value for attempt in attempts)
    assert "PRIVATE-MODEL-DETAIL" not in history.to_json()


@pytest.mark.integration
async def test_fifth_decision_request_exhausts_the_budget(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    settings = workflow_settings(test_database_url, tmp_path, max_decisions=4)
    run_id, workflow_id = await submit_run(database_engine, key="workflow-budget")
    worker = await create_worker(settings, model=NeverFinishModel())
    client = await temporal_client(settings)

    async with worker:
        with pytest.raises(WorkflowFailureError):
            await client.execute_workflow(
                SupportReportWorkflow.run,
                WorkflowInput(
                    run_id=run_id,
                    goal="Find recurring support themes",
                    ticket_files=("ticket-001.txt", "ticket-002.txt"),
                    max_decisions=4,
                ),
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
            )

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
    history = await client.get_workflow_handle(workflow_id).fetch_history()
    assert run is not None and run.state == RunState.BUDGET_EXHAUSTED.value
    assert activity_count(history.to_json_dict(), "decide_action") == 4


@pytest.mark.integration
async def test_denied_ticket_path_is_a_permanent_failed_outcome(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    settings = workflow_settings(test_database_url, tmp_path)
    run_id, workflow_id = await submit_run(
        database_engine, key="workflow-denied", ticket_files=("missing.txt",)
    )
    worker = await create_worker(settings)
    client = await temporal_client(settings)

    async with worker:
        with pytest.raises(WorkflowFailureError):
            await client.execute_workflow(
                SupportReportWorkflow.run,
                WorkflowInput(
                    run_id=run_id,
                    goal="Find recurring support themes",
                    ticket_files=("missing.txt",),
                    max_decisions=4,
                ),
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
            )

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
    assert run is not None and run.state == RunState.FAILED.value
    assert run.failure_kind == "PERMANENT"


@pytest.mark.integration
async def test_invalid_model_output_fails_once_without_executing_an_action(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    settings = workflow_settings(test_database_url, tmp_path)
    run_id, workflow_id = await submit_run(database_engine, key="workflow-invalid")
    worker = await create_worker(settings, model=InvalidOutputModel())
    client = await temporal_client(settings)

    async with worker:
        with pytest.raises(WorkflowFailureError):
            await client.execute_workflow(
                SupportReportWorkflow.run,
                WorkflowInput(
                    run_id=run_id,
                    goal="Find recurring support themes",
                    ticket_files=("ticket-001.txt", "ticket-002.txt"),
                    max_decisions=4,
                ),
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
            )

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
        step_names = set((await session.scalars(select(StepRecord.name))).all())
    history = await client.get_workflow_handle(workflow_id).fetch_history()
    assert run is not None and run.state == RunState.FAILED.value
    assert run.failure_kind == "PERMANENT"
    assert activity_count(history.to_json_dict(), "decide_action") == 1
    assert step_names == {"decide_action"}


@pytest.mark.integration
async def test_model_cannot_expand_the_submitted_ticket_allowlist(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    settings = workflow_settings(test_database_url, tmp_path)
    run_id, workflow_id = await submit_run(
        database_engine,
        key="workflow-unauthorized-read",
        ticket_files=("ticket-001.txt",),
    )
    worker = await create_worker(settings, model=UnauthorizedReadModel())
    client = await temporal_client(settings)

    async with worker:
        with pytest.raises(WorkflowFailureError):
            await client.execute_workflow(
                SupportReportWorkflow.run,
                WorkflowInput(
                    run_id=run_id,
                    goal="Find recurring support themes",
                    ticket_files=("ticket-001.txt",),
                    max_decisions=4,
                ),
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
            )

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        run = await session.get(RunRecord, run_id)
        step_names = set((await session.scalars(select(StepRecord.name))).all())
    history = await client.get_workflow_handle(workflow_id).fetch_history()
    assert run is not None and run.state == RunState.FAILED.value
    assert run.failure_kind == "PERMANENT"
    assert activity_count(history.to_json_dict(), "read_tickets") == 0
    assert step_names == {"decide_action"}


@pytest.mark.integration
async def test_activity_retry_preserves_step_and_adds_an_attempt(
    database_engine: AsyncEngine,
    test_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = workflow_settings(test_database_url, tmp_path)
    run_id, workflow_id = await submit_run(database_engine, key="workflow-retry")
    store = ArtifactStorage(
        ticket_root=settings.ticket_root,
        artifact_root=settings.artifact_root,
        max_ticket_bytes=settings.max_ticket_bytes,
        max_total_ticket_bytes=settings.max_total_ticket_bytes,
    )
    original_snapshot = store.snapshot_tickets
    calls = 0

    def fail_once(run_id: UUID, ticket_files: tuple[str, ...]):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient read failure")
        return original_snapshot(run_id, ticket_files)

    monkeypatch.setattr(store, "snapshot_tickets", fail_once)
    worker = await create_worker(
        settings, storage=store, inbox_client=RecordingInboxClient()
    )
    client = await temporal_client(settings)
    async with worker:
        handle = await client.start_workflow(
            SupportReportWorkflow.run,
            WorkflowInput(
                run_id=run_id,
                goal="Find recurring support themes",
                ticket_files=("ticket-001.txt", "ticket-002.txt"),
                max_decisions=4,
            ),
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
        )
        approval = await wait_for_approval(database_engine, run_id)
        approved = await decide_approval(
            database_engine, approval, ApprovalDecision.APPROVE
        )
        await handle.signal(SupportReportWorkflow.approval_decided, approved)
        await handle.result()

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        read_step = (
            await session.scalars(
                select(StepRecord).where(StepRecord.name == "read_tickets")
            )
        ).one()
        attempts = list(
            (
                await session.scalars(
                    select(AttemptRecord)
                    .where(AttemptRecord.step_id == read_step.id)
                    .order_by(AttemptRecord.attempt_number)
                )
            ).all()
        )
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]


@pytest.mark.integration
async def test_replayed_projection_does_not_duplicate_attempt_or_regress_state(
    database_engine: AsyncEngine,
) -> None:
    run_id, _ = await submit_run(database_engine, key="projection-attempt")
    step_id = uuid4()
    attempt_id = uuid4()
    terminal = ProjectionEvent(
        event_id=uuid4(),
        run_id=run_id,
        sequence=5,
        run_state=RunState.SUCCEEDED,
        step_id=step_id,
        step_ordinal=0,
        step_name="decide_action",
        step_state=StepState.SUCCEEDED,
        attempt_id=attempt_id,
        attempt_number=1,
        attempt_state=AttemptState.SUCCEEDED,
    )
    stale = ProjectionEvent(
        event_id=uuid4(), run_id=run_id, sequence=4, run_state=RunState.RUNNING
    )
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        assert await ProjectionRepository.apply(session, terminal) is True
    async with sessions() as session:
        assert await ProjectionRepository.apply(session, terminal) is False
    async with sessions() as session:
        assert await ProjectionRepository.apply(session, stale) is False
    async with sessions() as session:
        attempt_count = await session.scalar(
            select(func.count()).select_from(AttemptRecord)
        )
        run = await session.get(RunRecord, run_id)
    assert attempt_count == 1
    assert run is not None and run.state == RunState.SUCCEEDED.value
