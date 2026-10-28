from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

import agentforge.domain as domain

from agentforge.domain import (
    AgentAction,
    ArtifactRef,
    AttemptState,
    DraftRef,
    FailureKind,
    FinishAction,
    PublishReportAction,
    ReadTicketsAction,
    RunState,
    StepState,
    TicketSnapshotRef,
)


SHA256 = "a" * 64
RUN_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_state_enum_values_match_the_documented_flow() -> None:
    assert {state.value for state in RunState} == {
        "PENDING_START",
        "RUNNING",
        "WAITING_APPROVAL",
        "CANCELLATION_REQUESTED",
        "SUCCEEDED",
        "FAILED",
        "DENIED",
        "CANCELLED",
        "TIMED_OUT",
        "BUDGET_EXHAUSTED",
        "NEEDS_ATTENTION",
    }
    assert {state.value for state in StepState} == {
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "WAITING_APPROVAL",
        "RETRY_SCHEDULED",
        "FAILED",
        "DENIED",
        "CANCELLED",
        "UNCERTAIN",
    }
    assert {state.value for state in AttemptState} == {
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
    }
    assert {kind.value for kind in FailureKind} == {
        "RETRYABLE",
        "PERMANENT",
        "DENIED",
        "CANCELLED",
        "UNCERTAIN",
    }


def test_agent_action_accepts_exactly_the_four_discriminated_variants() -> None:
    adapter = TypeAdapter(AgentAction)
    ticket_snapshot = TicketSnapshotRef(
        path="snapshots/run-1/tickets.json", sha256=SHA256, size_bytes=12, ticket_count=2
    )
    draft = DraftRef(path="drafts/run-1/report.md", sha256=SHA256, size_bytes=20)
    artifact = ArtifactRef(
        path="reports/run-1/support-report.md",
        sha256=SHA256,
        size_bytes=20,
        media_type="text/markdown",
    )

    assert isinstance(
        adapter.validate_python(
            {"type": "read_tickets", "ticket_files": ["ticket-001.txt"]}
        ),
        ReadTicketsAction,
    )
    assert isinstance(
        adapter.validate_python(
            {"type": "publish_report", "ticket_snapshot": ticket_snapshot, "draft": draft}
        ),
        PublishReportAction,
    )
    assert isinstance(
        adapter.validate_python({"type": "finish", "artifact": artifact}), FinishAction
    )
    delivered = adapter.validate_python(
        {
            "type": "deliver_report",
            "target": "simulated-inbox",
            "artifact": artifact,
        }
    )
    assert isinstance(delivered, domain.DeliverReportAction)
    assert delivered.model_dump(mode="json") == {
        "type": "deliver_report",
        "target": "simulated-inbox",
        "artifact": {
            "path": "reports/run-1/support-report.md",
            "sha256": SHA256,
            "size_bytes": 20,
            "media_type": "text/markdown",
        },
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "unknown"},
        {"type": "read_tickets"},
        {"type": "read_tickets", "ticket_files": [123]},
        {"type": "finish", "artifact": {"path": "report.md"}},
        {
            "type": "deliver_report",
            "target": "external-inbox",
            "artifact": {
                "path": "reports/run-1/support-report.md",
                "sha256": SHA256,
                "size_bytes": 20,
                "media_type": "text/markdown",
            },
        },
        {
            "type": "deliver_report",
            "target": "simulated-inbox",
            "artifact": {
                "path": "reports/run-1/support-report.md",
                "sha256": SHA256,
                "size_bytes": 20,
                "media_type": "text/markdown",
            },
            "url": "http://127.0.0.1:8001",
        },
    ],
)
def test_agent_action_rejects_unknown_or_malformed_variants(payload: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(AgentAction).validate_python(payload)


@pytest.mark.parametrize(
    ("model", "valid"),
    [
        (
            TicketSnapshotRef,
            {
                "path": "snapshots/run-1/tickets.json",
                "sha256": SHA256,
                "size_bytes": 12,
                "ticket_count": 2,
            },
        ),
        (
            DraftRef,
            {"path": "drafts/run-1/report.md", "sha256": SHA256, "size_bytes": 20},
        ),
        (
            ArtifactRef,
            {
                "path": "reports/run-1/support-report.md",
                "sha256": SHA256,
                "size_bytes": 20,
                "media_type": "text/markdown",
            },
        ),
    ],
)
@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("size_bytes", -1),
        ("sha256", "not-a-sha256"),
        ("path", "C:/outside/report.md"),
        ("path", "/outside/report.md"),
    ],
)
def test_reference_models_reject_invalid_metadata(
    model: type[TicketSnapshotRef | DraftRef | ArtifactRef],
    valid: dict[str, object],
    field: str,
    invalid: object,
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({**valid, field: invalid})


def test_durable_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ArtifactRef(
            path="reports/run-1/support-report.md",
            sha256=SHA256,
            size_bytes=20,
            media_type="text/markdown",
            absolute_path="E:/outside/report.md",
        )


def test_approval_and_delivery_contracts_are_strict() -> None:
    required = {
        "ApprovalState",
        "ApprovalDecision",
        "DeliveryAuthorization",
        "ApprovalSnapshot",
        "ApprovalDecisionRequest",
        "ApprovalSignal",
        "CancellationSignal",
        "DeliveryRequest",
        "DeliveryReceipt",
    }
    assert required <= set(vars(domain))

    artifact = ArtifactRef(
        path="reports/run-1/support-report.md",
        sha256=SHA256,
        size_bytes=20,
        media_type="text/markdown",
    )
    request = domain.DeliveryRequest(
        operation_id=UUID("00000000-0000-0000-0000-000000000010"),
        action_hash="b" * 64,
        artifact=artifact,
        content="# Support Report\n",
    )
    assert request.content == "# Support Report\n"
    with pytest.raises(ValidationError):
        domain.DeliveryRequest(
            operation_id=request.operation_id,
            action_hash=request.action_hash,
            artifact=artifact,
            content=request.content,
            callback_url="http://example.test",
        )


def test_model_profile_snapshot_is_strict_and_immutable() -> None:
    profile_type = getattr(domain, "ModelProfileSnapshot", None)
    assert profile_type is not None

    profile = profile_type(
        profile_id="mock-v1",
        provider="mock",
        model_name="deterministic-support-report-v1",
        model_digest=None,
        task_queue="agentforge-support-report-v1",
        context_tokens=0,
        max_output_tokens=0,
        max_prompt_chars=0,
        max_report_chars=0,
        max_repairs=0,
    )

    assert profile.model_dump(mode="json") == {
        "profile_id": "mock-v1",
        "provider": "mock",
        "model_name": "deterministic-support-report-v1",
        "model_digest": None,
        "task_queue": "agentforge-support-report-v1",
        "context_tokens": 0,
        "max_output_tokens": 0,
        "max_prompt_chars": 0,
        "max_report_chars": 0,
        "max_repairs": 0,
    }
    with pytest.raises(ValidationError):
        profile_type.model_validate({**profile.model_dump(), "base_url": "secret"})
    with pytest.raises(ValidationError):
        profile_type(
            **{
                **profile.model_dump(),
                "provider": "cloud",
            }
        )


def test_model_profile_descriptor_contains_only_public_identity() -> None:
    descriptor_type = getattr(domain, "ModelProfileDescriptor", None)
    assert descriptor_type is not None

    descriptor = descriptor_type(
        profile_id="ollama-qwen3-4b-instruct-q4-v1",
        display_name="Local Qwen3 4B",
        provider="ollama",
        model_name="qwen3:4b-instruct-2507-q4_K_M",
        model_digest="a" * 64,
    )

    assert set(descriptor.model_dump()) == {
        "profile_id",
        "display_name",
        "provider",
        "model_name",
        "model_digest",
    }
