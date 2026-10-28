from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from agentforge.db_models import (
    AgentVersionRecord,
    AttemptRecord,
    RunRecord,
    StepRecord,
    WorkflowStartOutboxRecord,
)
from agentforge.domain import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalState,
    ArtifactRef,
    AttemptState,
    DeliverReportAction,
    DeliveryReceipt,
    FailureKind,
    ModelIdentity,
    ModelProfileSnapshot,
    ModelResponseUsage,
    ProjectionEvent,
    RunState,
    StepState,
    SubmitRun,
)
from agentforge.model_profiles import MOCK_MODEL_PROFILE
from agentforge.multi_agent import SUPPORT_REPORT_GRAPH_V2
from agentforge.policy import authorize_delivery
import agentforge.db_models as db
import agentforge.repository as repositories
from agentforge.repository import (
    GraphLimitExceeded,
    ModelUsageRepository,
    ProjectionRepository,
    RunRepository,
    StaleModelUsageWrite,
    SubmissionConflict,
    record_activity_attempt,
)


async def seeded_agent_version_id(engine: AsyncEngine, version: int = 1) -> UUID:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        return (
            await session.scalars(
                select(AgentVersionRecord.id).where(
                    AgentVersionRecord.version == version
                )
            )
        ).one()


def submission(goal: str = "Summarize the support tickets") -> SubmitRun:
    return SubmitRun(
        submission_key="demo-001",
        goal=goal,
        ticket_files=("ticket-001.txt", "ticket-002.txt"),
    )


@pytest.mark.integration
async def test_first_submission_creates_one_run_and_one_outbox_row(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)

    async with sessions() as session:
        result = await RunRepository.submit(session, submission(), version_id)

    async with sessions() as session:
        run_count = await session.scalar(select(func.count()).select_from(RunRecord))
        outbox_count = await session.scalar(
            select(func.count()).select_from(WorkflowStartOutboxRecord)
        )

    assert result.created is True
    assert result.run.workflow_id == f"agentforge/run/{result.run.run_id}"
    assert result.run.state is RunState.PENDING_START
    assert run_count == 1
    assert outbox_count == 1


@pytest.mark.integration
async def test_equal_retry_returns_the_existing_run(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)

    async with sessions() as session:
        first = await RunRepository.submit(session, submission(), version_id)
    async with sessions() as session:
        second = await RunRepository.submit(session, submission(), version_id)

    assert second.created is False
    assert second.run.run_id == first.run.run_id


@pytest.mark.integration
async def test_changed_payload_with_the_same_key_conflicts(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)

    async with sessions() as session:
        await RunRepository.submit(session, submission(), version_id)
    async with sessions() as session:
        with pytest.raises(SubmissionConflict):
            await RunRepository.submit(session, submission("A different goal"), version_id)


@pytest.mark.integration
async def test_concurrent_equal_submissions_converge_on_one_run(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)

    async def submit_once():
        async with sessions() as session:
            return await RunRepository.submit(session, submission(), version_id)

    first, second = await asyncio.gather(submit_once(), submit_once())

    assert first.run.run_id == second.run.run_id
    assert {first.created, second.created} == {True, False}


@pytest.mark.integration
async def test_new_v2_submission_persists_exact_workflow_kind_and_limits(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine, version=2)

    async with sessions() as session:
        result = await RunRepository.submit(
            session,
            submission(),
            version_id,
            workflow_kind="support-report-v2",
            graph_limits=SUPPORT_REPORT_GRAPH_V2,
        )
    async with sessions() as session:
        run = await session.get(RunRecord, result.run.run_id)
        outbox = (
            await session.scalars(
                select(WorkflowStartOutboxRecord).where(
                    WorkflowStartOutboxRecord.run_id == result.run.run_id
                )
            )
        ).one()

    assert run is not None and run.agent_version_id == version_id
    assert outbox.payload["workflow_kind"] == "support-report-v2"
    assert outbox.payload["graph_limits"] == SUPPORT_REPORT_GRAPH_V2.model_dump(
        mode="json"
    )


@pytest.mark.integration
async def test_old_key_retry_returns_legacy_run_even_above_v2_ticket_limit(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_one = await seeded_agent_version_id(database_engine, version=1)
    version_two = await seeded_agent_version_id(database_engine, version=2)
    request = SubmitRun(
        submission_key="legacy-large-retry",
        goal="Summarize support tickets",
        ticket_files=tuple(f"ticket-{index}.txt" for index in range(17)),
    )
    async with sessions() as session:
        first = await RunRepository.submit(session, request, version_one)
    async with sessions() as session:
        original = await session.get(RunRecord, first.run.run_id)
    assert original is not None
    original_hash = original.request_hash
    async with sessions() as session:
        retry = await RunRepository.submit(
            session,
            request,
            version_two,
            workflow_kind="support-report-v2",
            graph_limits=SUPPORT_REPORT_GRAPH_V2,
        )
    async with sessions() as session:
        outbox = (
            await session.scalars(
                select(WorkflowStartOutboxRecord).where(
                    WorkflowStartOutboxRecord.run_id == first.run.run_id
                )
            )
        ).one()

    assert retry.created is False and retry.run.run_id == first.run.run_id
    current = await _run_record(database_engine, first.run.run_id)
    assert original.agent_version_id == version_one
    assert current.request_hash == original_hash
    assert "workflow_kind" not in outbox.payload
    assert "graph_limits" not in outbox.payload


async def _run_record(engine: AsyncEngine, run_id: UUID) -> RunRecord:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        record = await session.get(RunRecord, run_id)
    assert record is not None
    return record


@pytest.mark.integration
async def test_new_seventeen_file_submission_is_rejected_without_rows(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_two = await seeded_agent_version_id(database_engine, version=2)
    request = SubmitRun(
        submission_key="new-large-v2",
        goal="Summarize support tickets",
        ticket_files=tuple(f"ticket-{index}.txt" for index in range(17)),
    )

    async with sessions() as session:
        with pytest.raises(GraphLimitExceeded):
            await RunRepository.submit(
                session,
                request,
                version_two,
                workflow_kind="support-report-v2",
                graph_limits=SUPPORT_REPORT_GRAPH_V2,
            )
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(RunRecord)) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(WorkflowStartOutboxRecord)
            )
            == 0
        )


def ollama_profile(**overrides: object) -> ModelProfileSnapshot:
    values: dict[str, object] = {
        "profile_id": "ollama-qwen3-4b-instruct-q4-v1",
        "provider": "ollama",
        "model_name": "qwen3:4b-instruct-2507-q4_K_M",
        "model_digest": "a" * 64,
        "task_queue": "agentforge-support-report-ollama-qwen3-4b-v1",
        "context_tokens": 8_192,
        "max_output_tokens": 2_048,
        "max_prompt_chars": 24_000,
        "max_report_chars": 16_384,
        "max_repairs": 2,
    }
    values.update(overrides)
    return ModelProfileSnapshot.model_validate(values)


@pytest.mark.integration
async def test_model_profile_round_trips_through_run_and_start_outbox(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    profile = ollama_profile()
    request = SubmitRun(
        submission_key="profile-round-trip",
        goal="Summarize support tickets",
        ticket_files=("ticket-001.txt",),
        model_profile_id=profile.profile_id,
    )

    async with sessions() as session:
        first = await RunRepository.submit(
            session, request, version_id, model_profile=profile
        )
    async with sessions() as session:
        retry = await RunRepository.submit(
            session, request, version_id, model_profile=profile
        )
        detail = await RunRepository.get_detail(session, first.run.run_id)
        run = await session.get(RunRecord, first.run.run_id)
        outbox = (
            await session.scalars(
                select(WorkflowStartOutboxRecord).where(
                    WorkflowStartOutboxRecord.run_id == first.run.run_id
                )
            )
        ).one()

    assert retry.created is False
    assert retry.run.run_id == first.run.run_id
    assert detail is not None
    assert detail.model_profile == profile
    assert run is not None
    assert run.model_profile == profile.model_dump(mode="json")
    assert outbox.payload["model_profile"] == profile.model_dump(mode="json")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_digest", "b" * 64),
        ("max_prompt_chars", 23_999),
        ("task_queue", "agentforge-support-report-ollama-qwen3-4b-v2"),
    ],
)
async def test_submission_key_conflicts_when_profile_snapshot_changes(
    database_engine: AsyncEngine, field: str, value: object
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    request = SubmitRun(
        submission_key=f"profile-conflict-{field}",
        goal="Summarize support tickets",
        ticket_files=("ticket-001.txt",),
        model_profile_id="ollama-qwen3-4b-instruct-q4-v1",
    )
    original = ollama_profile()
    changed = ollama_profile(**{field: value})

    async with sessions() as session:
        await RunRepository.submit(
            session, request, version_id, model_profile=original
        )
    async with sessions() as session:
        with pytest.raises(SubmissionConflict):
            await RunRepository.submit(
                session, request, version_id, model_profile=changed
            )


@pytest.mark.integration
async def test_attempt_model_usage_is_nullable_bounded_and_isolated(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(
            session,
            SubmitRun(
                submission_key="attempt-model-usage",
                goal="Summarize support tickets",
                ticket_files=("ticket-001.txt",),
            ),
            version_id,
            model_profile=MOCK_MODEL_PROFILE,
        )

    for attempt_number in (1, 2):
        async with sessions() as session:
            await record_activity_attempt(
                session,
                run_id=submitted.run.run_id,
                ordinal=1,
                name="decide_action",
                attempt_number=attempt_number,
                attempt_state=AttemptState.RUNNING,
                step_state=StepState.RUNNING,
            )

    async with sessions() as session:
        attempts = list(
            (
                await session.scalars(
                    select(AttemptRecord).order_by(AttemptRecord.attempt_number)
                )
            ).all()
        )
        first, second = attempts
        first.model_provider = "ollama"
        first.model_name = "qwen3:4b-instruct-2507-q4_K_M"
        first.model_digest = "c" * 64
        first.model_request_count = 2
        first.model_input_tokens = 100
        first.model_output_tokens = 25
        first.model_duration_ms = 1_250
        first.provider_cost_estimate_usd = Decimal("0.000000")
        first.input_tokens_estimated = False
        first.usage_uncertain = False
        await session.commit()

    async with sessions() as session:
        detail = await RunRepository.get_detail(session, submitted.run.run_id)
    assert detail is not None
    first_snapshot, second_snapshot = detail.steps[0].attempts
    assert first_snapshot.model_provider == "ollama"
    assert first_snapshot.model_request_count == 2
    assert first_snapshot.model_input_tokens == 100
    assert first_snapshot.model_output_tokens == 25
    assert first_snapshot.model_duration_ms == 1_250
    assert first_snapshot.provider_cost_estimate_usd == 0
    assert first_snapshot.input_tokens_estimated is False
    assert first_snapshot.usage_uncertain is False
    assert second_snapshot.model_provider is None
    assert second_snapshot.model_request_count is None

    async with sessions() as session:
        second = (
            await session.scalars(
                select(AttemptRecord).where(AttemptRecord.attempt_number == 2)
            )
        ).one()
        second.model_request_count = -1
        with pytest.raises(IntegrityError):
            await session.commit()


def usage_identity(**overrides: object) -> ModelIdentity:
    values: dict[str, object] = {
        "provider": "ollama",
        "model_name": "qwen3:4b-instruct-2507-q4_K_M",
        "model_digest": "c" * 64,
    }
    values.update(overrides)
    return ModelIdentity.model_validate(values)


async def create_usage_attempt(
    sessions: async_sessionmaker,
    run_id: UUID,
    attempt_number: int,
) -> None:
    async with sessions() as session:
        await record_activity_attempt(
            session,
            run_id=run_id,
            ordinal=1,
            name="decide_action",
            attempt_number=attempt_number,
            attempt_state=AttemptState.RUNNING,
            step_state=StepState.RUNNING,
        )


@pytest.mark.integration
async def test_model_usage_replaces_each_estimate_with_exact_aggregate(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(session, submission(), version_id)
    await create_usage_attempt(sessions, submitted.run.run_id, 1)
    identity = usage_identity()

    async with sessions() as session:
        await ModelUsageRepository.request_started(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=1,
            identity=identity,
            estimated_input_tokens=120,
        )
    async with sessions() as session:
        started = (await session.scalars(select(AttemptRecord))).one()
        assert started.model_request_count == 1
        assert started.model_input_tokens == 120
        assert started.model_output_tokens is None
        assert started.model_duration_ms is None
        assert started.provider_cost_estimate_usd == Decimal("0.000000")
        assert started.input_tokens_estimated is True
        assert started.usage_uncertain is True

    async with sessions() as session:
        await ModelUsageRepository.response_completed(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=1,
            estimated_input_tokens=120,
            exact_usage=ModelResponseUsage(
                input_tokens=100, output_tokens=25, duration_ms=1_250
            ),
        )
    async with sessions() as session:
        await ModelUsageRepository.request_started(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=1,
            identity=identity,
            estimated_input_tokens=80,
        )
    async with sessions() as session:
        await ModelUsageRepository.response_completed(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=1,
            estimated_input_tokens=80,
            exact_usage=ModelResponseUsage(
                input_tokens=60, output_tokens=10, duration_ms=500
            ),
        )
    async with sessions() as session:
        await record_activity_attempt(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            name="decide_action",
            attempt_number=1,
            attempt_state=AttemptState.SUCCEEDED,
            step_state=StepState.SUCCEEDED,
        )

    async with sessions() as session:
        completed = (await session.scalars(select(AttemptRecord))).one()
    assert completed.model_provider == "ollama"
    assert completed.model_name == identity.model_name
    assert completed.model_digest == identity.model_digest
    assert completed.model_request_count == 2
    assert completed.model_input_tokens == 160
    assert completed.model_output_tokens == 35
    assert completed.model_duration_ms == 1_750
    assert completed.provider_cost_estimate_usd == Decimal("0.000000")
    assert completed.input_tokens_estimated is False
    assert completed.usage_uncertain is False


@pytest.mark.integration
async def test_ambiguous_usage_cannot_increase_graph_repairs_or_activity_attempts(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(session, submission(), version_id)
    await create_usage_attempt(sessions, submitted.run.run_id, 1)

    async with sessions() as session:
        await ModelUsageRepository.request_started(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=1,
            identity=usage_identity(),
            estimated_input_tokens=77,
        )
    async with sessions() as session:
        await ModelUsageRepository.response_completed(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=1,
            estimated_input_tokens=77,
            exact_usage=ModelResponseUsage(
                input_tokens=None, output_tokens=None, duration_ms=None
            ),
        )

    async with sessions() as session:
        attempt = (await session.scalars(select(AttemptRecord))).one()
    assert attempt.model_input_tokens == 77
    assert attempt.model_request_count == 1
    assert attempt.attempt_number == 1
    assert attempt.model_output_tokens is None
    assert attempt.model_duration_ms is None
    assert attempt.input_tokens_estimated is True
    assert attempt.usage_uncertain is True


@pytest.mark.integration
async def test_model_usage_keeps_earlier_unmeasured_request_uncertain(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(session, submission(), version_id)
    await create_usage_attempt(sessions, submitted.run.run_id, 1)
    identity = usage_identity()

    async with sessions() as session:
        await ModelUsageRepository.request_started(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=1,
            identity=identity,
            estimated_input_tokens=120,
        )
    async with sessions() as session:
        await ModelUsageRepository.request_started(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=1,
            identity=identity,
            estimated_input_tokens=80,
        )
    async with sessions() as session:
        await ModelUsageRepository.response_completed(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=1,
            estimated_input_tokens=80,
            exact_usage=ModelResponseUsage(
                input_tokens=60, output_tokens=10, duration_ms=500
            ),
        )

    async with sessions() as session:
        attempt = (await session.scalars(select(AttemptRecord))).one()
    assert attempt.model_request_count == 2
    assert attempt.model_input_tokens == 180
    assert attempt.model_output_tokens is None
    assert attempt.model_duration_ms is None
    assert attempt.input_tokens_estimated is True
    assert attempt.usage_uncertain is True


@pytest.mark.integration
async def test_model_usage_preserves_crash_evidence_and_rejects_stale_attempt(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(session, submission(), version_id)
    identity = usage_identity()
    await create_usage_attempt(sessions, submitted.run.run_id, 1)
    async with sessions() as session:
        await ModelUsageRepository.request_started(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=1,
            identity=identity,
            estimated_input_tokens=88,
        )
    await create_usage_attempt(sessions, submitted.run.run_id, 2)
    async with sessions() as session:
        await ModelUsageRepository.request_started(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=2,
            identity=identity,
            estimated_input_tokens=50,
        )

    async with sessions() as session:
        with pytest.raises(StaleModelUsageWrite):
            await ModelUsageRepository.response_completed(
                session,
                run_id=submitted.run.run_id,
                ordinal=1,
                attempt_number=1,
                estimated_input_tokens=88,
                exact_usage=ModelResponseUsage(
                    input_tokens=70, output_tokens=5, duration_ms=100
                ),
            )

    async with sessions() as session:
        attempts = list(
            (
                await session.scalars(
                    select(AttemptRecord).order_by(AttemptRecord.attempt_number)
                )
            ).all()
        )
    assert attempts[0].model_request_count == 1
    assert attempts[0].model_input_tokens == 88
    assert attempts[0].usage_uncertain is True
    assert attempts[1].model_request_count == 1
    assert attempts[1].model_input_tokens == 50
    assert attempts[1].usage_uncertain is True


@pytest.mark.integration
async def test_model_usage_locks_concurrent_increments_and_rejects_identity_change(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(session, submission(), version_id)
    await create_usage_attempt(sessions, submitted.run.run_id, 1)
    identity = usage_identity()

    async def start(estimate: int) -> None:
        async with sessions() as session:
            await ModelUsageRepository.request_started(
                session,
                run_id=submitted.run.run_id,
                ordinal=1,
                attempt_number=1,
                identity=identity,
                estimated_input_tokens=estimate,
            )

    await asyncio.gather(start(20), start(30))
    async with sessions() as session:
        with pytest.raises(ValueError, match="model identity mismatch"):
            await ModelUsageRepository.request_started(
                session,
                run_id=submitted.run.run_id,
                ordinal=1,
                attempt_number=1,
                identity=usage_identity(model_digest="d" * 64),
                estimated_input_tokens=10,
            )

    async with sessions() as session:
        attempt = (await session.scalars(select(AttemptRecord))).one()
    assert attempt.model_request_count == 2
    assert attempt.model_input_tokens == 50
    assert attempt.usage_uncertain is True


@pytest.mark.integration
async def test_model_usage_rejects_non_model_steps(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(session, submission(), version_id)
    async with sessions() as session:
        await record_activity_attempt(
            session,
            run_id=submitted.run.run_id,
            ordinal=2,
            name="read_tickets",
            attempt_number=1,
            attempt_state=AttemptState.RUNNING,
            step_state=StepState.RUNNING,
        )

    async with sessions() as session:
        with pytest.raises(ValueError, match="model role"):
            await ModelUsageRepository.request_started(
                session,
                run_id=submitted.run.run_id,
                ordinal=2,
                attempt_number=1,
                identity=usage_identity(),
                estimated_input_tokens=10,
            )


@pytest.mark.integration
@pytest.mark.parametrize(
    "step_name",
    [
        "decide_action",
        "research_shard_1",
        "research_shard_4",
        "write_report_r0",
        "write_report_r2",
        "review_report_r0",
        "review_report_r2",
    ],
)
async def test_role_usage_is_durable_for_all_allowed_role_step_names(
    database_engine: AsyncEngine, step_name: str
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(session, submission(), version_id)
    async with sessions() as session:
        await record_activity_attempt(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            name=step_name,
            attempt_number=1,
            attempt_state=AttemptState.RUNNING,
            step_state=StepState.RUNNING,
        )
    async with sessions() as session:
        await ModelUsageRepository.request_started(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            attempt_number=1,
            identity=usage_identity(),
            estimated_input_tokens=10,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "step_name",
    [
        "research_shard_0",
        "research_shard_5",
        "research_shard_1_extra",
        "write_report_r3",
        "review_report_r-1",
    ],
)
async def test_usage_rejects_near_match_or_out_of_range_role_step_names(
    database_engine: AsyncEngine, step_name: str
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(session, submission(), version_id)
    async with sessions() as session:
        await record_activity_attempt(
            session,
            run_id=submitted.run.run_id,
            ordinal=1,
            name=step_name,
            attempt_number=1,
            attempt_state=AttemptState.RUNNING,
            step_state=StepState.RUNNING,
        )
    async with sessions() as session:
        with pytest.raises(ValueError, match="model role"):
            await ModelUsageRepository.request_started(
                session,
                run_id=submitted.run.run_id,
                ordinal=1,
                attempt_number=1,
                identity=usage_identity(),
                estimated_input_tokens=10,
            )


@pytest.mark.integration
async def test_projection_events_are_idempotent_and_cannot_move_state_backward(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(session, submission(), version_id)

    newer = ProjectionEvent(
        event_id=uuid4(),
        run_id=submitted.run.run_id,
        sequence=2,
        run_state=RunState.WAITING_APPROVAL,
    )
    older = ProjectionEvent(
        event_id=uuid4(),
        run_id=submitted.run.run_id,
        sequence=1,
        run_state=RunState.RUNNING,
    )

    async with sessions() as session:
        assert await ProjectionRepository.apply(session, newer) is True
    async with sessions() as session:
        assert await ProjectionRepository.apply(session, newer) is False
    async with sessions() as session:
        assert await ProjectionRepository.apply(session, older) is False
    async with sessions() as session:
        current = await RunRepository.get(session, submitted.run.run_id)

    assert current is not None
    assert current.state is RunState.WAITING_APPROVAL


@pytest.mark.integration
async def test_starting_retry_marks_older_running_attempt_failed_retryable(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(session, submission(), version_id)

    for attempt_number in (1, 2):
        async with sessions() as session:
            await record_activity_attempt(
                session,
                run_id=submitted.run.run_id,
                ordinal=2,
                name="decide_action",
                attempt_number=attempt_number,
                attempt_state=AttemptState.RUNNING,
                step_state=StepState.RUNNING,
            )

    async with sessions() as session:
        step_count = await session.scalar(select(func.count()).select_from(StepRecord))
        attempts = list(
            (
                await session.scalars(
                    select(AttemptRecord).order_by(AttemptRecord.attempt_number)
                )
            ).all()
        )

    assert step_count == 1
    assert [attempt.state for attempt in attempts] == [
        AttemptState.FAILED.value,
        AttemptState.RUNNING.value,
    ]
    assert [attempt.failure_kind for attempt in attempts] == [
        FailureKind.RETRYABLE.value,
        None,
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("newer_attempt_starts_first", "expected_older_state", "expected_failure"),
    [
        (True, AttemptState.FAILED.value, FailureKind.RETRYABLE.value),
        (False, AttemptState.SUCCEEDED.value, None),
    ],
)
async def test_older_terminal_update_cannot_override_newer_attempt(
    database_engine: AsyncEngine,
    *,
    newer_attempt_starts_first: bool,
    expected_older_state: str,
    expected_failure: str | None,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    async with sessions() as session:
        submitted = await RunRepository.submit(session, submission(), version_id)

    async def record(attempt_number: int, attempt_state: AttemptState) -> None:
        async with sessions() as session:
            await record_activity_attempt(
                session,
                run_id=submitted.run.run_id,
                ordinal=2,
                name="decide_action",
                attempt_number=attempt_number,
                attempt_state=attempt_state,
                step_state=(
                    StepState.SUCCEEDED
                    if attempt_state is AttemptState.SUCCEEDED
                    else StepState.RUNNING
                ),
            )

    await record(1, AttemptState.RUNNING)
    if newer_attempt_starts_first:
        await record(2, AttemptState.RUNNING)
        await record(1, AttemptState.SUCCEEDED)
    else:
        await record(1, AttemptState.SUCCEEDED)
        await record(2, AttemptState.RUNNING)

    async with sessions() as session:
        step = (await session.scalars(select(StepRecord))).one()
        attempts = list(
            (
                await session.scalars(
                    select(AttemptRecord).order_by(AttemptRecord.attempt_number)
                )
            ).all()
        )

    assert [attempt.state for attempt in attempts] == [
        expected_older_state,
        AttemptState.RUNNING.value,
    ]
    assert [attempt.failure_kind for attempt in attempts] == [
        expected_failure,
        None,
    ]
    assert step.state == StepState.RUNNING.value


async def submitted_run(engine: AsyncEngine, key: str):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(engine)
    async with sessions() as session:
        return await RunRepository.submit(
            session,
            SubmitRun(
                submission_key=key,
                goal="Summarize support tickets",
                ticket_files=("ticket-001.txt",),
            ),
            version_id,
        )


def delivery_authorization(run_id: UUID, *, sha256: str = "a" * 64):
    artifact = ArtifactRef(
        path="reports/run/support-report.md",
        sha256=sha256,
        size_bytes=20,
        media_type="text/markdown",
    )
    action = DeliverReportAction(
        type="deliver_report", target="simulated-inbox", artifact=artifact
    )
    return authorize_delivery(run_id, action, artifact)


@pytest.mark.integration
async def test_pending_approval_is_idempotent_and_changed_action_is_distinct(
    database_engine: AsyncEngine,
) -> None:
    assert hasattr(repositories, "ApprovalRepository")
    submitted = await submitted_run(database_engine, "approval-create")
    first_authorization = delivery_authorization(submitted.run.run_id)
    changed_authorization = delivery_authorization(
        submitted.run.run_id, sha256="b" * 64
    )
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)

    async with sessions() as session:
        first = await repositories.ApprovalRepository.create_pending(
            session,
            run_id=submitted.run.run_id,
            authorization=first_authorization,
            expires_at=expires_at,
        )
    async with sessions() as session:
        duplicate = await repositories.ApprovalRepository.create_pending(
            session,
            run_id=submitted.run.run_id,
            authorization=first_authorization,
            expires_at=expires_at,
        )
    async with sessions() as session:
        changed = await repositories.ApprovalRepository.create_pending(
            session,
            run_id=submitted.run.run_id,
            authorization=changed_authorization,
            expires_at=expires_at,
        )

    assert first == duplicate
    assert first.state is ApprovalState.PENDING
    assert first.action == first_authorization.action
    assert changed.approval_id != first.approval_id
    assert changed.operation_id != first.operation_id


@pytest.mark.integration
async def test_first_approval_decision_and_signal_intent_win(
    database_engine: AsyncEngine,
) -> None:
    assert hasattr(repositories, "ApprovalRepository")
    assert hasattr(db, "WorkflowSignalOutboxRecord")
    submitted = await submitted_run(database_engine, "approval-decision")
    authorization = delivery_authorization(submitted.run.run_id)
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        await repositories.ApprovalRepository.create_pending(
            session,
            run_id=submitted.run.run_id,
            authorization=authorization,
            expires_at=expires_at,
        )
    request = ApprovalDecisionRequest(
        action_hash=authorization.action_hash,
        decision=ApprovalDecision.APPROVE,
    )
    async with sessions() as session:
        first = await repositories.ApprovalRepository.decide(
            session, authorization.approval_id, request
        )
    async with sessions() as session:
        duplicate = await repositories.ApprovalRepository.decide(
            session, authorization.approval_id, request
        )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.approval.state is ApprovalState.APPROVED
    async with sessions() as session:
        signals = list(
            (await session.scalars(select(db.WorkflowSignalOutboxRecord))).all()
        )
    assert len(signals) == 1
    assert signals[0].signal_name == "approval_decided"
    assert signals[0].payload["action_hash"] == authorization.action_hash

    with pytest.raises(repositories.ApprovalConflict):
        async with sessions() as session:
            await repositories.ApprovalRepository.decide(
                session,
                authorization.approval_id,
                ApprovalDecisionRequest(
                    action_hash=authorization.action_hash,
                    decision=ApprovalDecision.REJECT,
                ),
            )
    with pytest.raises(repositories.ApprovalConflict):
        async with sessions() as session:
            await repositories.ApprovalRepository.decide(
                session,
                authorization.approval_id,
                ApprovalDecisionRequest(
                    action_hash="c" * 64,
                    decision=ApprovalDecision.APPROVE,
                ),
            )


@pytest.mark.integration
async def test_decision_and_expiry_race_follow_the_locked_winner(
    database_engine: AsyncEngine,
) -> None:
    assert hasattr(db, "ApprovalRecord")
    submitted = await submitted_run(database_engine, "approval-race")
    authorization = delivery_authorization(submitted.run.run_id)
    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        await repositories.ApprovalRepository.create_pending(
            session,
            run_id=submitted.run.run_id,
            authorization=authorization,
            expires_at=expired_at,
        )

    first = sessions()
    second = sessions()
    try:
        async with first.begin():
            record = await first.scalar(
                select(db.ApprovalRecord)
                .where(db.ApprovalRecord.id == authorization.approval_id)
                .with_for_update()
            )
            pending_expiry = asyncio.create_task(
                repositories.ApprovalRepository.resolve_or_expire(
                    second,
                    authorization.approval_id,
                    authorization.action_hash,
                    now=datetime.now(UTC),
                )
            )
            await asyncio.sleep(0.05)
            assert pending_expiry.done() is False
            assert record is not None
            record.state = ApprovalState.APPROVED.value
            record.decided_at = datetime.now(UTC)
        winner = await pending_expiry
    finally:
        await first.close()
        await second.close()
    assert winner.state is ApprovalState.APPROVED

    submitted = await submitted_run(database_engine, "expiry-race")
    authorization = delivery_authorization(submitted.run.run_id)
    async with sessions() as session:
        await repositories.ApprovalRepository.create_pending(
            session,
            run_id=submitted.run.run_id,
            authorization=authorization,
            expires_at=expired_at,
        )
    first = sessions()
    second = sessions()
    try:
        async with first.begin():
            record = await first.scalar(
                select(db.ApprovalRecord)
                .where(db.ApprovalRecord.id == authorization.approval_id)
                .with_for_update()
            )
            pending_decision = asyncio.create_task(
                repositories.ApprovalRepository.decide(
                    second,
                    authorization.approval_id,
                    ApprovalDecisionRequest(
                        action_hash=authorization.action_hash,
                        decision=ApprovalDecision.APPROVE,
                    ),
                )
            )
            await asyncio.sleep(0.05)
            assert pending_decision.done() is False
            assert record is not None
            record.state = ApprovalState.EXPIRED.value
        with pytest.raises(repositories.ApprovalConflict):
            await pending_decision
    finally:
        await first.close()
        await second.close()


@pytest.mark.integration
async def test_cancellation_is_idempotent_and_rejects_terminal_runs(
    database_engine: AsyncEngine,
) -> None:
    assert hasattr(repositories, "CancellationRepository")
    submitted = await submitted_run(database_engine, "cancellation")
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        first = await repositories.CancellationRepository.request(
            session, submitted.run.run_id
        )
    async with sessions() as session:
        duplicate = await repositories.CancellationRepository.request(
            session, submitted.run.run_id
        )
    assert first.created is True
    assert duplicate.created is False
    assert duplicate.cancellation == first.cancellation
    async with sessions() as session:
        signal_count = await session.scalar(
            select(func.count()).select_from(db.WorkflowSignalOutboxRecord)
        )
        run = await session.get(RunRecord, submitted.run.run_id)
        assert run is not None
        run.state = RunState.SUCCEEDED.value
        await session.commit()
    assert signal_count == 1
    with pytest.raises(repositories.CancellationConflict):
        async with sessions() as session:
            await repositories.CancellationRepository.request(
                session, submitted.run.run_id
            )


@pytest.mark.integration
async def test_accepted_delivery_receipt_is_idempotent_and_conflict_safe(
    database_engine: AsyncEngine,
) -> None:
    assert hasattr(repositories, "DeliveryRepository")
    submitted = await submitted_run(database_engine, "delivery-receipt")
    authorization = delivery_authorization(submitted.run.run_id)
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        await repositories.ApprovalRepository.create_pending(
            session,
            run_id=submitted.run.run_id,
            authorization=authorization,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    receipt = DeliveryReceipt(
        delivery_id=uuid4(),
        operation_id=authorization.operation_id,
        artifact_sha256=authorization.action.artifact.sha256,
        committed_at=datetime.now(UTC),
    )
    async with sessions() as session:
        first = await repositories.DeliveryRepository.record_receipt(
            session,
            run_id=submitted.run.run_id,
            approval_id=authorization.approval_id,
            receipt=receipt,
        )
    async with sessions() as session:
        duplicate = await repositories.DeliveryRepository.record_receipt(
            session,
            run_id=submitted.run.run_id,
            approval_id=authorization.approval_id,
            receipt=receipt,
        )
    assert first.created is True
    assert duplicate.created is False
    assert duplicate.receipt == receipt

    with pytest.raises(repositories.DeliveryConflict):
        async with sessions() as session:
            await repositories.DeliveryRepository.record_receipt(
                session,
                run_id=submitted.run.run_id,
                approval_id=authorization.approval_id,
                receipt=receipt.model_copy(update={"delivery_id": uuid4()}),
            )


@pytest.mark.integration
async def test_list_recent_returns_bounded_newest_runs(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    timestamps = [
        datetime(2026, 10, 20, 9, 0, tzinfo=UTC),
        datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        datetime(2026, 10, 22, 9, 0, tzinfo=UTC),
    ]
    run_ids: list[UUID] = []

    for index, created_at in enumerate(timestamps, start=1):
        request = SubmitRun(
            submission_key=f"recent-{index}",
            goal=f"Dashboard goal {index}",
            ticket_files=(f"ticket-00{index}.txt",),
        )
        async with sessions() as session:
            submitted = await RunRepository.submit(session, request, version_id)
        run_ids.append(submitted.run.run_id)
        async with sessions.begin() as session:
            record = await session.get(RunRecord, submitted.run.run_id)
            assert record is not None
            record.created_at = created_at
            record.updated_at = created_at + timedelta(minutes=index)
            if index == 2:
                record.state = RunState.FAILED.value
                record.failure_kind = FailureKind.PERMANENT.value

    async with sessions() as session:
        recent = await RunRepository.list_recent(session, 2)

    assert [item.run_id for item in recent] == [run_ids[2], run_ids[1]]
    assert [item.goal for item in recent] == ["Dashboard goal 3", "Dashboard goal 2"]
    assert [item.state for item in recent] == [RunState.PENDING_START, RunState.FAILED]
    assert [item.failure_kind for item in recent] == [None, FailureKind.PERMANENT]
    assert recent[0].created_at == timestamps[2]
    assert recent[0].updated_at == timestamps[2] + timedelta(minutes=3)


@pytest.mark.integration
@pytest.mark.parametrize("limit", [0, 51])
async def test_list_recent_rejects_out_of_bounds_limit(
    database_engine: AsyncEngine, limit: int
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)

    async with sessions() as session:
        with pytest.raises(ValueError, match="between 1 and 50"):
            await RunRepository.list_recent(session, limit)


@pytest.mark.integration
async def test_run_detail_returns_ordered_nested_durable_state(
    database_engine: AsyncEngine,
) -> None:
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    version_id = await seeded_agent_version_id(database_engine)
    request = SubmitRun(
        submission_key="run-detail",
        goal="Find recurring support themes",
        ticket_files=("ticket-001.txt", "ticket-002.txt"),
    )
    async with sessions() as session:
        submitted = await RunRepository.submit(session, request, version_id)
    run_id = submitted.run.run_id
    step_one_id = uuid4()
    step_two_id = uuid4()
    base_time = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)

    older_authorization = delivery_authorization(run_id, sha256="a" * 64)
    latest_authorization = delivery_authorization(run_id, sha256="b" * 64)
    for authorization in (older_authorization, latest_authorization):
        async with sessions() as session:
            await repositories.ApprovalRepository.create_pending(
                session,
                run_id=run_id,
                authorization=authorization,
                expires_at=base_time + timedelta(hours=2),
            )

    async with sessions() as session:
        cancellation = await repositories.CancellationRepository.request(session, run_id)
    receipt = DeliveryReceipt(
        delivery_id=uuid4(),
        operation_id=latest_authorization.operation_id,
        artifact_sha256="b" * 64,
        committed_at=base_time + timedelta(minutes=30),
    )
    async with sessions() as session:
        await repositories.DeliveryRepository.record_receipt(
            session,
            run_id=run_id,
            approval_id=latest_authorization.approval_id,
            receipt=receipt,
        )

    async with sessions.begin() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        run.state = RunState.WAITING_APPROVAL.value
        run.projection_sequence = 7
        run.updated_at = base_time + timedelta(minutes=20)
        session.add(
            db.ArtifactRecord(
                id=uuid4(),
                run_id=run_id,
                path="reports/run/support-report.md",
                media_type="text/markdown",
                sha256="b" * 64,
                size_bytes=20,
                created_at=base_time + timedelta(minutes=15),
            )
        )
        session.add_all(
            [
                StepRecord(
                    id=step_two_id,
                    run_id=run_id,
                    ordinal=2,
                    name="publish_report",
                    state=StepState.WAITING_APPROVAL.value,
                    created_at=base_time + timedelta(minutes=10),
                    updated_at=base_time + timedelta(minutes=20),
                ),
                StepRecord(
                    id=step_one_id,
                    run_id=run_id,
                    ordinal=1,
                    name="read_tickets",
                    state=StepState.SUCCEEDED.value,
                    created_at=base_time,
                    updated_at=base_time + timedelta(minutes=8),
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                AttemptRecord(
                    id=uuid4(),
                    step_id=step_one_id,
                    attempt_number=2,
                    state=AttemptState.SUCCEEDED.value,
                    failure_kind=None,
                    created_at=base_time + timedelta(minutes=4),
                    updated_at=base_time + timedelta(minutes=8),
                ),
                AttemptRecord(
                    id=uuid4(),
                    step_id=step_one_id,
                    attempt_number=1,
                    state=AttemptState.FAILED.value,
                    failure_kind=FailureKind.RETRYABLE.value,
                    created_at=base_time,
                    updated_at=base_time + timedelta(minutes=3),
                ),
            ]
        )
        older = await session.get(db.ApprovalRecord, older_authorization.approval_id)
        latest = await session.get(db.ApprovalRecord, latest_authorization.approval_id)
        assert older is not None
        assert latest is not None
        older.created_at = base_time + timedelta(minutes=11)
        latest.created_at = base_time + timedelta(minutes=12)

    async with sessions() as session:
        detail = await RunRepository.get_detail(session, run_id)
        missing = await RunRepository.get_detail(session, uuid4())

    assert missing is None
    assert detail is not None
    assert detail.goal == "Find recurring support themes"
    assert detail.ticket_files == ("ticket-001.txt", "ticket-002.txt")
    assert detail.projection_sequence == 7
    assert detail.run.state is RunState.WAITING_APPROVAL
    assert detail.run.artifact == latest_authorization.action.artifact
    assert [step.ordinal for step in detail.steps] == [1, 2]
    assert [attempt.attempt_number for attempt in detail.steps[0].attempts] == [1, 2]
    assert detail.steps[0].attempts[0].failure_kind is FailureKind.RETRYABLE
    assert detail.steps[1].attempts == ()
    assert detail.approval is not None
    assert detail.approval.approval_id == latest_authorization.approval_id
    assert detail.cancellation_requested is True
    assert cancellation.cancellation.run_id == run_id
    assert detail.delivery == receipt
