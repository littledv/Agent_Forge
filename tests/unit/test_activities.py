from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from temporalio.exceptions import ApplicationError

import agentforge.worker as worker_module
from agentforge.activities import AgentActivities, DecideActionInput, DeliverReportInput
from agentforge.config import Settings
from agentforge.domain import (
    AgentAction,
    ArtifactRef,
    DeliverReportAction,
    DeliveryAuthorization,
    FailureKind,
    ModelDecisionInput,
    ModelProfileSnapshot,
    ReadTicketsAction,
    StepState,
)
from agentforge.model_profiles import MOCK_MODEL_PROFILE
from agentforge.ollama_model import (
    ModelBudgetExceeded,
    ModelConfigurationError,
    ModelOutputInvalid,
    ModelRequestRejected,
    ModelTransportError,
    OLLAMA_VERSION,
)
from agentforge.storage import StorageViolation
from agentforge.repository import ModelUsageRepository
from agentforge.worker import create_worker


class OversizedStorage:
    max_total_ticket_bytes = 300_000

    def read_verified_artifact(self, artifact: ArtifactRef) -> bytes:
        return ("PRIVATE-REPORT-CONTENT" + "x" * 300_000).encode("utf-8")


class UnusedModel:
    async def decide(self, input: object, usage: object) -> object:
        raise AssertionError("model should not be called")


class UnusedInbox:
    def deliver(self, request: object) -> object:
        raise AssertionError("invalid content must not reach the inbox")


@pytest.mark.asyncio
async def test_oversized_delivery_content_is_rejected_without_leaking_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = ArtifactRef(
        path="reports/run/support-report.md",
        sha256="a" * 64,
        size_bytes=300_022,
        media_type="text/markdown",
    )
    authorization = DeliveryAuthorization(
        approval_id=UUID("00000000-0000-0000-0000-000000000001"),
        operation_id=UUID("00000000-0000-0000-0000-000000000002"),
        action_hash="b" * 64,
        action=DeliverReportAction(
            type="deliver_report",
            target="simulated-inbox",
            artifact=artifact,
        ),
    )
    activities = AgentActivities(
        None,  # type: ignore[arg-type]
        OversizedStorage(),  # type: ignore[arg-type]
        UnusedModel(),  # type: ignore[arg-type]
        UnusedInbox(),  # type: ignore[arg-type]
    )
    activities._record = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(
        "agentforge.activities.activity.info", lambda: SimpleNamespace(attempt=1)
    )

    with pytest.raises(ApplicationError) as caught:
        await activities.deliver_report(
            DeliverReportInput(
                run_id=UUID("00000000-0000-0000-0000-000000000003"),
                step_ordinal=7,
                authorization=authorization,
            )
        )

    assert caught.value.non_retryable
    assert caught.value.type == "ValidationError"
    assert "PRIVATE-REPORT-CONTENT" not in str(caught.value)


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


class CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, input: ModelDecisionInput, usage: object) -> AgentAction:
        self.calls += 1
        raise AssertionError("mismatched profile must not reach provider")


class RaisingModel:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def decide(self, input: ModelDecisionInput, usage: object) -> AgentAction:
        raise self.error


class UsageCallingModel:
    async def decide(self, input: ModelDecisionInput, usage: object) -> AgentAction:
        await usage.request_started(estimated_input_tokens=101)  # type: ignore[attr-defined]
        await usage.response_completed(  # type: ignore[attr-defined]
            estimated_input_tokens=101,
            input_tokens=91,
            output_tokens=12,
            duration_ms=345,
        )
        return ReadTicketsAction(type="read_tickets", ticket_files=input.ticket_files)


class FakeSessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeSessions:
    def __call__(self) -> FakeSessionContext:
        return FakeSessionContext()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_digest", "b" * 64),
        ("max_prompt_chars", 23_999),
        ("task_queue", "agentforge-support-report-ollama-qwen3-4b-v2"),
    ],
)
async def test_profile_mismatch_fails_before_async_provider_call(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    expected = ollama_profile()
    actual = ollama_profile(**{field: value})
    model = CountingModel()
    activities = AgentActivities(
        None,  # type: ignore[arg-type]
        OversizedStorage(),  # type: ignore[arg-type]
        model,  # type: ignore[arg-type]
        UnusedInbox(),  # type: ignore[arg-type]
        model_profile=expected,
    )
    activities._record = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(
        "agentforge.activities.activity.info", lambda: SimpleNamespace(attempt=1)
    )

    with pytest.raises(ApplicationError) as caught:
        await activities.decide_action(
            DecideActionInput(
                decision=ModelDecisionInput(
                    run_id=UUID("00000000-0000-0000-0000-000000000004"),
                    goal="Summarize tickets",
                    ticket_files=("ticket-001.txt",),
                    decision_number=1,
                    model_profile=actual,
                ),
                step_ordinal=0,
            )
        )

    assert caught.value.non_retryable
    assert caught.value.type == "ModelProfileMismatch"
    assert actual.model_digest not in str(caught.value)
    assert model.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (ModelRequestRejected("model request rejected"), "ModelRequestRejected"),
        (ModelOutputInvalid("repair_exhausted"), "ModelOutputInvalid"),
        (ModelConfigurationError("model identity mismatch"), "ModelConfigurationError"),
        (StorageViolation("private path detail"), "StorageViolation"),
    ],
)
async def test_permanent_model_failures_are_sanitized_and_nonretryable(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected_type: str
) -> None:
    activities = AgentActivities(
        None,  # type: ignore[arg-type]
        OversizedStorage(),  # type: ignore[arg-type]
        RaisingModel(error),  # type: ignore[arg-type]
        UnusedInbox(),  # type: ignore[arg-type]
    )
    activities._record = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(
        "agentforge.activities.activity.info", lambda: SimpleNamespace(attempt=1)
    )

    with pytest.raises(ApplicationError) as caught:
        await activities.decide_action(
            DecideActionInput(
                decision=ModelDecisionInput(
                    run_id=UUID("00000000-0000-0000-0000-000000000030"),
                    goal="Summarize tickets",
                    ticket_files=("ticket-001.txt",),
                    decision_number=1,
                ),
                step_ordinal=0,
            )
        )

    assert caught.value.non_retryable
    assert caught.value.type == expected_type
    assert str(error) not in str(caught.value)
    assert activities._record.await_args_list[-1].kwargs["failure_kind"] is FailureKind.PERMANENT
    assert activities._record.await_args_list[-1].kwargs["step_state"] is StepState.FAILED


@pytest.mark.asyncio
async def test_transport_failure_is_sanitized_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = ModelTransportError("private transport detail")
    activities = AgentActivities(
        None,  # type: ignore[arg-type]
        OversizedStorage(),  # type: ignore[arg-type]
        RaisingModel(error),  # type: ignore[arg-type]
        UnusedInbox(),  # type: ignore[arg-type]
    )
    activities._record = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(
        "agentforge.activities.activity.info", lambda: SimpleNamespace(attempt=1)
    )

    with pytest.raises(ApplicationError) as caught:
        await activities.decide_action(
            DecideActionInput(
                decision=ModelDecisionInput(
                    run_id=UUID("00000000-0000-0000-0000-000000000031"),
                    goal="Summarize tickets",
                    ticket_files=("ticket-001.txt",),
                    decision_number=1,
                ),
                step_ordinal=0,
            )
        )

    assert caught.value.non_retryable is False
    assert caught.value.type == "ModelTransportError"
    assert "private transport detail" not in str(caught.value)
    assert activities._record.await_args_list[-1].kwargs["failure_kind"] is FailureKind.RETRYABLE
    assert (
        activities._record.await_args_list[-1].kwargs["step_state"]
        is StepState.RETRY_SCHEDULED
    )


@pytest.mark.asyncio
async def test_model_budget_failure_is_nonretryable_with_distinct_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activities = AgentActivities(
        None,  # type: ignore[arg-type]
        OversizedStorage(),  # type: ignore[arg-type]
        RaisingModel(ModelBudgetExceeded("private prompt detail")),  # type: ignore[arg-type]
        UnusedInbox(),  # type: ignore[arg-type]
    )
    activities._record = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(
        "agentforge.activities.activity.info", lambda: SimpleNamespace(attempt=1)
    )

    with pytest.raises(ApplicationError) as caught:
        await activities.decide_action(
            DecideActionInput(
                decision=ModelDecisionInput(
                    run_id=UUID("00000000-0000-0000-0000-000000000032"),
                    goal="Summarize tickets",
                    ticket_files=("ticket-001.txt",),
                    decision_number=1,
                ),
                step_ordinal=0,
            )
        )

    assert caught.value.non_retryable
    assert caught.value.type == "ModelBudgetExceeded"
    assert "private prompt detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_ollama_activity_uses_attempt_scoped_durable_usage_reporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ollama_profile()
    started = AsyncMock()
    completed = AsyncMock()
    monkeypatch.setattr(ModelUsageRepository, "request_started", started)
    monkeypatch.setattr(ModelUsageRepository, "response_completed", completed)
    monkeypatch.setattr(
        "agentforge.activities.activity.info", lambda: SimpleNamespace(attempt=3)
    )
    activities = AgentActivities(
        FakeSessions(),  # type: ignore[arg-type]
        OversizedStorage(),  # type: ignore[arg-type]
        UsageCallingModel(),  # type: ignore[arg-type]
        UnusedInbox(),  # type: ignore[arg-type]
        model_profile=profile,
    )
    activities._record = AsyncMock()  # type: ignore[method-assign]
    run_id = UUID("00000000-0000-0000-0000-000000000033")

    action = await activities.decide_action(
        DecideActionInput(
            decision=ModelDecisionInput(
                run_id=run_id,
                goal="Summarize tickets",
                ticket_files=("ticket-001.txt",),
                decision_number=1,
                model_profile=profile,
            ),
            step_ordinal=4,
        )
    )

    assert isinstance(action, ReadTicketsAction)
    assert started.await_args.kwargs["run_id"] == run_id
    assert started.await_args.kwargs["ordinal"] == 4
    assert started.await_args.kwargs["attempt_number"] == 3
    assert started.await_args.kwargs["identity"].model_digest == "a" * 64
    assert started.await_args.kwargs["estimated_input_tokens"] == 101
    assert completed.await_args.kwargs["exact_usage"].input_tokens == 91
    assert completed.await_args.kwargs["exact_usage"].output_tokens == 12
    assert completed.await_args.kwargs["exact_usage"].duration_ms == 345


@pytest.mark.asyncio
async def test_mock_activity_never_writes_model_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = AsyncMock()
    completed = AsyncMock()
    monkeypatch.setattr(ModelUsageRepository, "request_started", started)
    monkeypatch.setattr(ModelUsageRepository, "response_completed", completed)
    monkeypatch.setattr(
        "agentforge.activities.activity.info", lambda: SimpleNamespace(attempt=1)
    )
    activities = AgentActivities(
        FakeSessions(),  # type: ignore[arg-type]
        OversizedStorage(),  # type: ignore[arg-type]
        UsageCallingModel(),  # type: ignore[arg-type]
        UnusedInbox(),  # type: ignore[arg-type]
    )
    activities._record = AsyncMock()  # type: ignore[method-assign]

    await activities.decide_action(
        DecideActionInput(
            decision=ModelDecisionInput(
                run_id=UUID("00000000-0000-0000-0000-000000000034"),
                goal="Summarize tickets",
                ticket_files=("ticket-001.txt",),
                decision_number=1,
            ),
            step_ordinal=0,
        )
    )

    started.assert_not_awaited()
    completed.assert_not_awaited()


class CapturedWorker:
    instances = 0

    def __init__(self, client: object, **kwargs: object) -> None:
        type(self).instances += 1
        self.task_queue = kwargs["task_queue"]
        self.activities = kwargs["activities"]
        self.workflows = kwargs["workflows"]


@pytest.mark.asyncio
async def test_profile_workers_poll_separate_immutable_queues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_connect = AsyncMock(return_value=object())
    monkeypatch.setattr(worker_module.Client, "connect", async_connect)
    monkeypatch.setattr(worker_module, "Worker", CapturedWorker)
    monkeypatch.setattr(
        worker_module,
        "create_engine_and_sessionmaker",
        lambda settings: (object(), object()),
    )
    common: dict[str, object] = {
        "database_url": "postgresql+psycopg://local/agentforge",
        "temporal_address": "127.0.0.1:7233",
        "temporal_namespace": "default",
        "temporal_task_queue": "mutable-process-queue",
        "ticket_root": tmp_path,
        "artifact_root": tmp_path,
        "max_ticket_bytes": 65_536,
        "max_total_ticket_bytes": 262_144,
        "enabled_model_profiles": (
            "mock-v1",
            "ollama-qwen3-4b-instruct-q4-v1",
        ),
        "ollama_model_digest": "a" * 64,
    }
    mock_settings = Settings.model_validate(
        {**common, "worker_model_profile": "mock-v1"}
    )
    ollama_settings = Settings.model_validate(
        {
            **common,
            "worker_model_profile": "ollama-qwen3-4b-instruct-q4-v1",
        }
    )
    fake_model = CountingModel()

    mock_worker = await create_worker(
        mock_settings,
        storage=OversizedStorage(),  # type: ignore[arg-type]
        model=fake_model,
        inbox_client=UnusedInbox(),
    )
    ollama_worker = await create_worker(
        ollama_settings,
        storage=OversizedStorage(),  # type: ignore[arg-type]
        model=fake_model,
        inbox_client=UnusedInbox(),
    )

    assert mock_worker.task_queue == MOCK_MODEL_PROFILE.task_queue
    assert ollama_worker.task_queue == ollama_profile().task_queue
    assert mock_worker.task_queue != ollama_worker.task_queue


@pytest.mark.asyncio
async def test_worker_registers_both_workflows_on_one_profile_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        worker_module.Client, "connect", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(worker_module, "Worker", CapturedWorker)
    monkeypatch.setattr(
        worker_module,
        "create_engine_and_sessionmaker",
        lambda settings: (object(), object()),
    )
    settings = Settings(
        database_url="postgresql+psycopg://local/agentforge",
        temporal_address="127.0.0.1:7233",
        temporal_namespace="default",
        temporal_task_queue="mutable-process-queue",
        ticket_root=tmp_path,
        artifact_root=tmp_path,
        max_ticket_bytes=65_536,
        max_total_ticket_bytes=262_144,
    )

    worker = await create_worker(
        settings,
        storage=OversizedStorage(),  # type: ignore[arg-type]
        model=CountingModel(),
        inbox_client=UnusedInbox(),
    )

    assert worker.task_queue == MOCK_MODEL_PROFILE.task_queue
    assert {workflow.__name__ for workflow in worker.workflows} == {
        "SupportReportWorkflow",
        "MultiAgentSupportReportWorkflow",
    }


@pytest.mark.asyncio
async def test_ollama_worker_verifies_runtime_before_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+psycopg://local/agentforge",
            "temporal_address": "127.0.0.1:7233",
            "temporal_namespace": "default",
            "temporal_task_queue": "mutable-process-queue",
            "ticket_root": tmp_path,
            "artifact_root": tmp_path,
            "max_ticket_bytes": 65_536,
            "max_total_ticket_bytes": 262_144,
            "enabled_model_profiles": (
                "mock-v1",
                "ollama-qwen3-4b-instruct-q4-v1",
            ),
            "worker_model_profile": "ollama-qwen3-4b-instruct-q4-v1",
            "ollama_model_digest": "a" * 64,
        }
    )
    monkeypatch.setattr(worker_module.Client, "connect", AsyncMock(return_value=object()))
    monkeypatch.setattr(worker_module, "Worker", CapturedWorker)
    monkeypatch.setattr(
        worker_module,
        "create_engine_and_sessionmaker",
        lambda settings: (object(), object()),
    )
    verification_calls: list[tuple[str, str, str]] = []

    class VerifiedClient:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["base_url"] == "http://127.0.0.1:11434"
            assert kwargs["read_timeout_seconds"] == 150

        async def verify_runtime(
            self, expected_version: str, model_name: str, model_digest: str
        ) -> None:
            verification_calls.append((expected_version, model_name, model_digest))

        async def generate_report(self, request: object, usage: object) -> object:
            raise AssertionError("provider must not run while constructing the Worker")

    monkeypatch.setattr(worker_module, "OllamaClient", VerifiedClient, raising=False)

    worker = await create_worker(
        settings,
        storage=OversizedStorage(),  # type: ignore[arg-type]
        inbox_client=UnusedInbox(),
    )

    assert verification_calls == [
        (OLLAMA_VERSION, "qwen3:4b-instruct-2507-q4_K_M", "a" * 64)
    ]
    assert worker.task_queue == ollama_profile().task_queue


@pytest.mark.asyncio
async def test_ollama_worker_does_not_poll_when_runtime_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+psycopg://local/agentforge",
            "temporal_address": "127.0.0.1:7233",
            "temporal_namespace": "default",
            "temporal_task_queue": "mutable-process-queue",
            "ticket_root": tmp_path,
            "artifact_root": tmp_path,
            "max_ticket_bytes": 65_536,
            "max_total_ticket_bytes": 262_144,
            "enabled_model_profiles": (
                "mock-v1",
                "ollama-qwen3-4b-instruct-q4-v1",
            ),
            "worker_model_profile": "ollama-qwen3-4b-instruct-q4-v1",
            "ollama_model_digest": "a" * 64,
        }
    )
    monkeypatch.setattr(worker_module.Client, "connect", AsyncMock(return_value=object()))
    monkeypatch.setattr(worker_module, "Worker", CapturedWorker)
    monkeypatch.setattr(
        worker_module,
        "create_engine_and_sessionmaker",
        lambda settings: (object(), object()),
    )

    class RejectedClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def verify_runtime(
            self, expected_version: str, model_name: str, model_digest: str
        ) -> None:
            raise ModelConfigurationError("Ollama model identity mismatch")

    monkeypatch.setattr(worker_module, "OllamaClient", RejectedClient, raising=False)
    prior_instances = CapturedWorker.instances

    with pytest.raises(ModelConfigurationError):
        await create_worker(
            settings,
            storage=OversizedStorage(),  # type: ignore[arg-type]
            inbox_client=UnusedInbox(),
        )

    assert CapturedWorker.instances == prior_instances
