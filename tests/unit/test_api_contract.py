from __future__ import annotations

from pathlib import Path

from agentforge.api import create_app
from agentforge.config import Settings


def _settings(tmp_path: Path) -> Settings:
    (tmp_path / "tickets").mkdir()
    (tmp_path / "artifacts").mkdir()
    return Settings(
        database_url=(
            "postgresql+psycopg://temporal:temporal-local-only@"
            "127.0.0.1:5432/agentforge_test"
        ),
        temporal_address="127.0.0.1:7233",
        temporal_namespace="agentforge-test",
        temporal_task_queue="agentforge-support-report-v1",
        ticket_root=tmp_path / "tickets",
        artifact_root=tmp_path / "artifacts",
        max_decisions=4,
        max_ticket_bytes=65_536,
        max_total_ticket_bytes=262_144,
    )


def _schema_ref(response: dict[str, object]) -> str:
    content = response["content"]
    assert isinstance(content, dict)
    media = content["application/json"]
    assert isinstance(media, dict)
    schema = media["schema"]
    assert isinstance(schema, dict)
    reference = schema.get("$ref")
    if reference is None:
        items = schema["items"]
        assert isinstance(items, dict)
        reference = items["$ref"]
    assert isinstance(reference, str)
    return reference


def test_v1_openapi_contract_has_stable_paths_methods_and_statuses(
    tmp_path: Path,
) -> None:
    schema = create_app(_settings(tmp_path)).openapi()
    paths = schema["paths"]
    assert set(paths) == {
        "/health/live",
        "/health/ready",
        "/api/v1/model-profiles",
        "/api/v1/runs",
        "/api/v1/runs/{run_id}",
        "/api/v1/runs/{run_id}/detail",
        "/api/v1/runs/{run_id}/approval",
        "/api/v1/approvals/{approval_id}/decision",
        "/api/v1/runs/{run_id}/cancel",
        "/api/v1/runs/{run_id}/artifact",
    }

    expected: dict[tuple[str, str], tuple[set[str], str]] = {
        ("/api/v1/runs", "post"): (
            {"200", "202", "409", "422", "503"},
            "RunSnapshot",
        ),
        ("/api/v1/model-profiles", "get"): ({"200"}, "ModelProfileDescriptor"),
        ("/api/v1/runs", "get"): (
            {"200", "422", "503"},
            "RunListItem",
        ),
        ("/api/v1/runs/{run_id}", "get"): (
            {"200", "404", "422", "503"},
            "RunSnapshot",
        ),
        ("/api/v1/runs/{run_id}/detail", "get"): (
            {"200", "404", "422", "503"},
            "RunDetailSnapshot",
        ),
        ("/api/v1/runs/{run_id}/approval", "get"): (
            {"200", "404", "422", "503"},
            "ApprovalSnapshot",
        ),
        ("/api/v1/approvals/{approval_id}/decision", "post"): (
            {"200", "202", "404", "409", "422", "503"},
            "ApprovalSnapshot",
        ),
        ("/api/v1/runs/{run_id}/cancel", "post"): (
            {"200", "202", "404", "409", "422", "503"},
            "CancellationSnapshot",
        ),
        ("/api/v1/runs/{run_id}/artifact", "get"): (
            {"200", "404", "409", "422", "503"},
            "ArtifactContent",
        ),
    }

    for (path, method), (statuses, success_schema) in expected.items():
        operation = paths[path][method]
        responses = operation["responses"]
        assert set(responses) == statuses
        for status_code in statuses:
            response_schema = _schema_ref(responses[status_code])
            if status_code in {"200", "202"}:
                assert response_schema == f"#/components/schemas/{success_schema}"
            else:
                assert response_schema == "#/components/schemas/ApiErrorResponse"

    error_schema = schema["components"]["schemas"]["ApiErrorResponse"]
    detail_schema = error_schema["properties"]["detail"]
    variants = detail_schema["anyOf"]
    assert {variant.get("type") for variant in variants} == {"string", "array"}


def test_v1_openapi_contract_freezes_required_response_fields_and_enums(
    tmp_path: Path,
) -> None:
    schemas = create_app(_settings(tmp_path)).openapi()["components"]["schemas"]
    required_fields = {
        "ModelProfileDescriptor": {
            "profile_id",
            "display_name",
            "provider",
            "model_name",
            "model_digest",
        },
        "RunSnapshot": {
            "run_id",
            "workflow_id",
            "state",
            "created_at",
            "updated_at",
        },
        "RunListItem": {
            "run_id",
            "goal",
            "state",
            "failure_kind",
            "created_at",
            "updated_at",
        },
        "RunDetailSnapshot": {
            "run",
            "goal",
            "ticket_files",
            "model_profile",
            "projection_sequence",
            "steps",
            "approval",
            "cancellation_requested",
            "delivery",
        },
        "ApprovalSnapshot": {
            "approval_id",
            "run_id",
            "operation_id",
            "action_hash",
            "action",
            "state",
            "expires_at",
            "created_at",
        },
        "CancellationSnapshot": {"request_id", "run_id", "created_at"},
        "ArtifactContent": {"artifact", "content"},
        "ArtifactRef": {"path", "sha256", "size_bytes", "media_type"},
        "ModelProfileSnapshot": {
            "profile_id",
            "provider",
            "model_name",
            "model_digest",
            "task_queue",
            "context_tokens",
            "max_output_tokens",
            "max_prompt_chars",
            "max_report_chars",
            "max_repairs",
        },
        "AttemptSnapshot": {
            "attempt_number",
            "state",
            "failure_kind",
            "created_at",
            "updated_at",
        },
        "StepSnapshot": {
            "ordinal",
            "name",
            "state",
            "created_at",
            "updated_at",
            "attempts",
        },
        "DeliveryReceipt": {
            "delivery_id",
            "operation_id",
            "artifact_sha256",
            "committed_at",
        },
        "DeliverReportAction": {"type", "target", "artifact"},
    }
    enum_values = {
        "RunState": {
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
        },
        "StepState": {
            "PENDING",
            "RUNNING",
            "SUCCEEDED",
            "WAITING_APPROVAL",
            "RETRY_SCHEDULED",
            "FAILED",
            "DENIED",
            "CANCELLED",
            "UNCERTAIN",
        },
        "AttemptState": {
            "PENDING",
            "RUNNING",
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
        },
        "FailureKind": {
            "RETRYABLE",
            "PERMANENT",
            "DENIED",
            "CANCELLED",
            "UNCERTAIN",
        },
        "ApprovalState": {"PENDING", "APPROVED", "REJECTED", "EXPIRED"},
        "ApprovalDecision": {"APPROVE", "REJECT"},
    }

    for schema_name, expected in required_fields.items():
        assert set(schemas[schema_name]["required"]) == expected
    for schema_name, expected in enum_values.items():
        assert set(schemas[schema_name]["enum"]) == expected
