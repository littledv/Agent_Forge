from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from agentforge.api import create_app
from agentforge.config import Settings
from agentforge.db_models import (
    AgentVersionRecord,
    ArtifactRecord,
    AttemptRecord,
    RunRecord,
    StepRecord,
    WorkflowSignalOutboxRecord,
)
from agentforge.domain import (
    ArtifactRef,
    AttemptState,
    DeliverReportAction,
    RunState,
    StepState,
    SubmitRun,
)
from agentforge.model_profiles import MOCK_MODEL_PROFILE
from agentforge.policy import authorize_delivery
from agentforge.repository import ApprovalRepository, RunRepository


def settings_for(
    database_url: str,
    *,
    artifact_root: Path | None = None,
    **overrides: object,
) -> Settings:
    values: dict[str, object] = {
        "database_url": database_url,
        "temporal_address": "127.0.0.1:7233",
        "temporal_namespace": "default",
        "temporal_task_queue": "agentforge-support-report-v1",
        "ticket_root": Path("samples/tickets").resolve(),
        "artifact_root": artifact_root or Path("artifacts").resolve(),
        "max_decisions": 4,
        "max_ticket_bytes": 65_536,
        "max_total_ticket_bytes": 262_144,
    }
    values.update(overrides)
    return Settings.model_validate(values)


@asynccontextmanager
async def api_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client


def valid_submission(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "submission_key": "api-demo-001",
        "goal": "Summarize the sample support tickets",
        "ticket_files": ["ticket-001.txt", "ticket-002.txt"],
    }
    payload.update(overrides)
    return payload


@pytest.mark.integration
async def test_submission_is_retry_safe(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        first = await client.post("/api/v1/runs", json=valid_submission())
        retry = await client.post("/api/v1/runs", json=valid_submission())

    assert first.status_code == 202
    assert retry.status_code == 200
    assert retry.json()["run_id"] == first.json()["run_id"]
    assert set(first.json()) == {
        "run_id",
        "workflow_id",
        "state",
        "created_at",
        "updated_at",
        "failure_kind",
        "artifact",
    }


@pytest.mark.integration
async def test_api_selects_agent_version_two_and_maps_graph_limit_to_422(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        accepted = await client.post(
            "/api/v1/runs",
            json=valid_submission(submission_key="api-version-two"),
        )
        rejected = await client.post(
            "/api/v1/runs",
            json=valid_submission(
                submission_key="api-too-many-tickets",
                ticket_files=[f"ticket-{index}.txt" for index in range(17)],
            ),
        )

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        selected_version = await session.scalar(
            select(AgentVersionRecord.version)
            .join(RunRecord, RunRecord.agent_version_id == AgentVersionRecord.id)
            .where(RunRecord.id == UUID(accepted.json()["run_id"]))
        )
        run_count = await session.scalar(select(func.count()).select_from(RunRecord))

    assert accepted.status_code == 202
    assert selected_version == 2
    assert rejected.status_code == 422
    assert rejected.json() == {"detail": "support report accepts at most 16 tickets"}
    assert run_count == 1


@pytest.mark.integration
async def test_changed_payload_with_reused_key_returns_conflict(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        await client.post("/api/v1/runs", json=valid_submission())
        response = await client.post(
            "/api/v1/runs", json=valid_submission(goal="A changed goal")
        )

    assert response.status_code == 409
    assert "database" not in response.text.lower()


@pytest.mark.integration
async def test_model_profiles_api_returns_only_enabled_public_descriptors(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    mock_app = create_app(settings_for(test_database_url))
    enabled_app = create_app(
        settings_for(
            test_database_url,
            enabled_model_profiles=(
                "mock-v1",
                "ollama-qwen3-4b-instruct-q4-v1",
            ),
            ollama_model_digest="d" * 64,
        )
    )

    async with api_client(mock_app) as client:
        mock_only = await client.get("/api/v1/model-profiles")
    async with api_client(enabled_app) as client:
        enabled = await client.get("/api/v1/model-profiles")

    assert mock_only.status_code == 200
    assert [item["profile_id"] for item in mock_only.json()] == ["mock-v1"]
    assert [item["profile_id"] for item in enabled.json()] == [
        "mock-v1",
        "ollama-qwen3-4b-instruct-q4-v1",
    ]
    assert "base_url" not in enabled.text
    assert "11434" not in enabled.text


@pytest.mark.integration
async def test_submission_resolves_enabled_profile_and_exposes_immutable_detail(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    digest = "e" * 64
    app = create_app(
        settings_for(
            test_database_url,
            enabled_model_profiles=(
                "mock-v1",
                "ollama-qwen3-4b-instruct-q4-v1",
            ),
            ollama_model_digest=digest,
        )
    )
    async with api_client(app) as client:
        submitted = await client.post(
            "/api/v1/runs",
            json=valid_submission(
                submission_key="api-model-profile",
                model_profile_id="ollama-qwen3-4b-instruct-q4-v1",
            ),
        )
        detail = await client.get(
            f"/api/v1/runs/{submitted.json()['run_id']}/detail"
        )

    assert submitted.status_code == 202
    assert detail.status_code == 200
    assert detail.json()["model_profile"] == {
        "profile_id": "ollama-qwen3-4b-instruct-q4-v1",
        "provider": "ollama",
        "model_name": "qwen3:4b-instruct-2507-q4_K_M",
        "model_digest": digest,
        "task_queue": "agentforge-support-report-ollama-qwen3-4b-v1",
        "context_tokens": 8_192,
        "max_output_tokens": 2_048,
        "max_prompt_chars": 24_000,
        "max_report_chars": 16_384,
        "max_repairs": 2,
    }


@pytest.mark.integration
async def test_submission_rejects_unknown_or_disabled_profile(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        unknown = await client.post(
            "/api/v1/runs",
            json=valid_submission(
                submission_key="api-unknown-profile",
                model_profile_id="unknown-v1",
            ),
        )
        disabled = await client.post(
            "/api/v1/runs",
            json=valid_submission(
                submission_key="api-disabled-profile",
                model_profile_id="ollama-qwen3-4b-instruct-q4-v1",
            ),
        )

    assert unknown.status_code == 422
    assert disabled.status_code == 422
    assert "digest" not in disabled.text.lower()


@pytest.mark.integration
async def test_submission_key_conflicts_across_enabled_profiles(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    app = create_app(
        settings_for(
            test_database_url,
            enabled_model_profiles=(
                "mock-v1",
                "ollama-qwen3-4b-instruct-q4-v1",
            ),
            ollama_model_digest="f" * 64,
        )
    )
    async with api_client(app) as client:
        first = await client.post(
            "/api/v1/runs", json=valid_submission(submission_key="profile-conflict")
        )
        conflict = await client.post(
            "/api/v1/runs",
            json=valid_submission(
                submission_key="profile-conflict",
                model_profile_id="ollama-qwen3-4b-instruct-q4-v1",
            ),
        )

    assert first.status_code == 202
    assert conflict.status_code == 409


@pytest.mark.integration
@pytest.mark.parametrize(
    "payload",
    [
        valid_submission(submission_key="bad key"),
        valid_submission(goal=""),
        valid_submission(ticket_files=[]),
        valid_submission(ticket_files=["C:/private/ticket.txt"]),
        valid_submission(unknown="value"),
    ],
)
async def test_malformed_submissions_are_rejected(
    database_engine: AsyncEngine, test_database_url: str, payload: dict[str, object]
) -> None:
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        response = await client.post("/api/v1/runs", json=payload)

    assert response.status_code == 422


@pytest.mark.integration
async def test_unknown_run_returns_not_found(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        response = await client.get(f"/api/v1/runs/{uuid4()}")

    assert response.status_code == 404


@pytest.mark.integration
async def test_v1_error_envelopes_match_documented_contract(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        missing = await client.get(f"/api/v1/runs/{uuid4()}")
        malformed_id = await client.get("/api/v1/runs/not-a-uuid")
        malformed_query = await client.get("/api/v1/runs?limit=0")
        submitted = await client.post(
            "/api/v1/runs",
            json=valid_submission(submission_key="api-v1-error-contract"),
        )
        conflict = await client.post(
            "/api/v1/runs",
            json=valid_submission(
                submission_key="api-v1-error-contract",
                goal="A changed request",
            ),
        )

    unavailable_app = create_app(
        settings_for(
            "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:1/"
            "agentforge"
        )
    )
    async with api_client(unavailable_app) as client:
        unavailable = await client.get("/health/ready")

    assert missing.status_code == 404
    assert missing.json() == {"detail": "run not found"}
    assert malformed_id.status_code == 422
    assert isinstance(malformed_id.json()["detail"], list)
    assert malformed_query.status_code == 422
    assert isinstance(malformed_query.json()["detail"], list)
    assert submitted.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json() == {
        "detail": "submission key already belongs to a different request"
    }
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "service unavailable"}
    assert "database" not in unavailable.text.lower()


@pytest.mark.integration
async def test_liveness_is_local_and_readiness_checks_postgresql(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    healthy_app = create_app(settings_for(test_database_url))
    async with api_client(healthy_app) as client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")

    unavailable_app = create_app(
        settings_for(
            "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:1/agentforge"
        )
    )
    async with api_client(unavailable_app) as client:
        unavailable = await client.get("/health/ready")

    assert live.status_code == 200
    assert ready.status_code == 200
    assert unavailable.status_code == 503
    assert "database" not in unavailable.text.lower()


async def create_pending_approval(
    engine: AsyncEngine,
    *,
    key: str,
    expired: bool = False,
) -> tuple[UUID, UUID, str]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        version_id = (
            await session.scalars(
                select(AgentVersionRecord.id).where(
                    AgentVersionRecord.version == 1
                )
            )
        ).one()
    async with sessions() as session:
        submitted = await RunRepository.submit(
            session,
            SubmitRun(
                submission_key=key,
                goal="Summarize support tickets",
                ticket_files=("ticket-001.txt",),
            ),
            version_id,
        )
    artifact = ArtifactRef(
        path="reports/run/support-report.md",
        sha256="a" * 64,
        size_bytes=20,
        media_type="text/markdown",
    )
    authorization = authorize_delivery(
        submitted.run.run_id,
        DeliverReportAction(
            type="deliver_report", target="simulated-inbox", artifact=artifact
        ),
        artifact,
    )
    async with sessions() as session:
        await ApprovalRepository.create_pending(
            session,
            run_id=submitted.run.run_id,
            authorization=authorization,
            expires_at=datetime.now(UTC)
            + (timedelta(seconds=-1) if expired else timedelta(minutes=5)),
        )
    return submitted.run.run_id, authorization.approval_id, authorization.action_hash


@pytest.mark.integration
async def test_approval_routes_return_exact_action_and_idempotent_decision(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    run_id, approval_id, action_hash = await create_pending_approval(
        database_engine, key="api-approval"
    )
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        pending = await client.get(f"/api/v1/runs/{run_id}/approval")
        first = await client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"action_hash": action_hash, "decision": "APPROVE"},
        )
        duplicate = await client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"action_hash": action_hash, "decision": "APPROVE"},
        )
        conflict = await client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"action_hash": action_hash, "decision": "REJECT"},
        )

    assert pending.status_code == 200
    assert pending.json()["action_hash"] == action_hash
    assert pending.json()["action"] == {
        "type": "deliver_report",
        "target": "simulated-inbox",
        "artifact": {
            "path": "reports/run/support-report.md",
            "sha256": "a" * 64,
            "size_bytes": 20,
            "media_type": "text/markdown",
        },
    }
    assert first.status_code == 202
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()
    assert conflict.status_code == 409
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        signal_count = await session.scalar(
            select(func.count()).select_from(WorkflowSignalOutboxRecord)
        )
    assert signal_count == 1


@pytest.mark.integration
async def test_approval_routes_reject_missing_changed_expired_and_malformed(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    _, approval_id, action_hash = await create_pending_approval(
        database_engine, key="api-expired", expired=True
    )
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        missing_get = await client.get(f"/api/v1/runs/{uuid4()}/approval")
        missing_decision = await client.post(
            f"/api/v1/approvals/{uuid4()}/decision",
            json={"action_hash": "a" * 64, "decision": "APPROVE"},
        )
        changed = await client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"action_hash": "c" * 64, "decision": "APPROVE"},
        )
        expired = await client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"action_hash": action_hash, "decision": "APPROVE"},
        )
        malformed = await client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"action_hash": action_hash, "decision": "MAYBE"},
        )

    assert missing_get.status_code == 404
    assert missing_decision.status_code == 404
    assert changed.status_code == 409
    assert expired.status_code == 409
    assert malformed.status_code == 422


@pytest.mark.integration
async def test_cancellation_route_is_idempotent_and_rejects_terminal_run(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    run_id, _, _ = await create_pending_approval(database_engine, key="api-cancel")
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        first = await client.post(f"/api/v1/runs/{run_id}/cancel")
        duplicate = await client.post(f"/api/v1/runs/{run_id}/cancel")
        missing = await client.post(f"/api/v1/runs/{uuid4()}/cancel")

    assert first.status_code == 202
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()
    assert missing.status_code == 404

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions.begin() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        run.state = RunState.SUCCEEDED.value
    async with api_client(app) as client:
        terminal = await client.post(f"/api/v1/runs/{run_id}/cancel")
    assert terminal.status_code == 409


@pytest.mark.integration
async def test_recent_runs_api_applies_default_explicit_and_invalid_limits(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    app = create_app(settings_for(test_database_url))
    timestamps = [
        datetime(2026, 10, 20, 9, 0, tzinfo=UTC),
        datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        datetime(2026, 10, 22, 9, 0, tzinfo=UTC),
    ]
    run_ids: list[str] = []
    async with api_client(app) as client:
        for index in range(1, 4):
            response = await client.post(
                "/api/v1/runs",
                json=valid_submission(
                    submission_key=f"api-recent-{index}",
                    goal=f"API dashboard goal {index}",
                ),
            )
            assert response.status_code == 202
            run_ids.append(response.json()["run_id"])

        sessions = async_sessionmaker(database_engine, expire_on_commit=False)
        async with sessions.begin() as session:
            for index, (run_id, created_at) in enumerate(
                zip(run_ids, timestamps, strict=True), start=1
            ):
                record = await session.get(RunRecord, UUID(run_id))
                assert record is not None
                record.created_at = created_at
                record.updated_at = created_at + timedelta(minutes=index)

        default = await client.get("/api/v1/runs")
        limited = await client.get("/api/v1/runs?limit=2")
        zero = await client.get("/api/v1/runs?limit=0")
        too_many = await client.get("/api/v1/runs?limit=51")

    assert default.status_code == 200
    assert len(default.json()) == 3
    assert limited.status_code == 200
    assert [item["run_id"] for item in limited.json()] == [run_ids[2], run_ids[1]]
    assert [item["goal"] for item in limited.json()] == [
        "API dashboard goal 3",
        "API dashboard goal 2",
    ]
    assert zero.status_code == 422
    assert too_many.status_code == 422


@pytest.mark.integration
async def test_run_detail_api_returns_nested_projection_shape(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        submitted = await client.post(
            "/api/v1/runs",
            json=valid_submission(submission_key="api-detail"),
        )
        assert submitted.status_code == 202
        run_id = UUID(submitted.json()["run_id"])
        step_id = uuid4()
        base_time = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
        sessions = async_sessionmaker(database_engine, expire_on_commit=False)
        async with sessions.begin() as session:
            run = await session.get(RunRecord, run_id)
            assert run is not None
            run.state = RunState.RUNNING.value
            run.projection_sequence = 3
            session.add(
                StepRecord(
                    id=step_id,
                    run_id=run_id,
                    ordinal=0,
                    name="read_tickets",
                    state=StepState.RUNNING.value,
                    created_at=base_time,
                    updated_at=base_time + timedelta(seconds=2),
                )
            )
            await session.flush()
            session.add(
                AttemptRecord(
                    id=uuid4(),
                    step_id=step_id,
                    attempt_number=1,
                    state=AttemptState.RUNNING.value,
                    created_at=base_time,
                    updated_at=base_time + timedelta(seconds=2),
                )
            )

        detail = await client.get(f"/api/v1/runs/{run_id}/detail")

    assert detail.status_code == 200
    payload = detail.json()
    assert set(payload) == {
        "run",
        "goal",
        "ticket_files",
        "model_profile",
        "projection_sequence",
        "steps",
        "approval",
        "cancellation_requested",
        "delivery",
    }
    assert payload["goal"] == "Summarize the sample support tickets"
    assert payload["ticket_files"] == ["ticket-001.txt", "ticket-002.txt"]
    assert payload["model_profile"] == MOCK_MODEL_PROFILE.model_dump(mode="json")
    assert payload["projection_sequence"] == 3
    assert payload["steps"] == [
        {
            "ordinal": 0,
            "name": "read_tickets",
            "state": "RUNNING",
            "created_at": base_time.isoformat().replace("+00:00", "Z"),
            "updated_at": (base_time + timedelta(seconds=2))
            .isoformat()
            .replace("+00:00", "Z"),
            "attempts": [
                {
                    "attempt_number": 1,
                    "state": "RUNNING",
                    "failure_kind": None,
                    "created_at": base_time.isoformat().replace("+00:00", "Z"),
                    "updated_at": (base_time + timedelta(seconds=2))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "model_provider": None,
                    "model_name": None,
                    "model_digest": None,
                    "model_request_count": None,
                    "model_input_tokens": None,
                    "model_output_tokens": None,
                    "model_duration_ms": None,
                    "provider_cost_estimate_usd": None,
                    "input_tokens_estimated": None,
                    "usage_uncertain": None,
                }
            ],
        }
    ]
    assert payload["approval"] is None
    assert payload["cancellation_requested"] is False
    assert payload["delivery"] is None


@pytest.mark.integration
async def test_run_deleted_between_list_and_detail_returns_sanitized_not_found(
    database_engine: AsyncEngine, test_database_url: str
) -> None:
    app = create_app(settings_for(test_database_url))
    async with api_client(app) as client:
        submitted = await client.post(
            "/api/v1/runs",
            json=valid_submission(submission_key="api-stale-selection"),
        )
        run_id = UUID(submitted.json()["run_id"])
        listed = await client.get("/api/v1/runs?limit=20")
        assert run_id in {UUID(item["run_id"]) for item in listed.json()}

        sessions = async_sessionmaker(database_engine, expire_on_commit=False)
        async with sessions.begin() as session:
            run = await session.get(RunRecord, run_id)
            assert run is not None
            await session.delete(run)

        missing = await client.get(f"/api/v1/runs/{run_id}/detail")
        unknown = await client.get(f"/api/v1/runs/{uuid4()}/detail")

    assert missing.status_code == 404
    assert missing.json() == {"detail": "run not found"}
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "run not found"}


async def create_artifact_run(
    engine: AsyncEngine,
    *,
    key: str,
    path: str | None = None,
    content: bytes | None = None,
    sha256: str | None = None,
    size_bytes: int | None = None,
    media_type: str = "text/markdown",
) -> UUID:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        version_id = (
            await session.scalars(
                select(AgentVersionRecord.id).where(
                    AgentVersionRecord.version == 1
                )
            )
        ).one()
    async with sessions() as session:
        submitted = await RunRepository.submit(
            session,
            SubmitRun(
                submission_key=key,
                goal="Inspect the support report",
                ticket_files=("ticket-001.txt",),
            ),
            version_id,
        )
    if path is not None:
        assert content is not None
        async with sessions.begin() as session:
            session.add(
                ArtifactRecord(
                    id=uuid4(),
                    run_id=submitted.run.run_id,
                    path=path,
                    media_type=media_type,
                    sha256=sha256 or hashlib.sha256(content).hexdigest(),
                    size_bytes=len(content) if size_bytes is None else size_bytes,
                )
            )
    return submitted.run.run_id


@pytest.mark.integration
async def test_artifact_content_returns_only_verified_recorded_markdown(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    artifact_root = tmp_path / "artifacts"
    content = b"# Support Report\n\nVerified content.\n"
    run_id = await create_artifact_run(
        database_engine,
        key="artifact-content-success",
        path="reports/verified/support-report.md",
        content=content,
    )
    report = artifact_root / "reports/verified/support-report.md"
    report.parent.mkdir(parents=True)
    report.write_bytes(content)
    app = create_app(
        settings_for(test_database_url, artifact_root=artifact_root)
    )

    async with api_client(app) as client:
        response = await client.get(f"/api/v1/runs/{run_id}/artifact")

    assert response.status_code == 200
    assert response.json() == {
        "artifact": {
            "path": "reports/verified/support-report.md",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "media_type": "text/markdown",
        },
        "content": content.decode("utf-8"),
    }


@pytest.mark.integration
async def test_artifact_content_returns_sanitized_not_found(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    run_without_artifact = await create_artifact_run(
        database_engine, key="artifact-content-absent"
    )
    app = create_app(
        settings_for(test_database_url, artifact_root=tmp_path / "artifacts")
    )

    async with api_client(app) as client:
        missing_run = await client.get(f"/api/v1/runs/{uuid4()}/artifact")
        missing_artifact = await client.get(
            f"/api/v1/runs/{run_without_artifact}/artifact"
        )

    assert missing_run.status_code == 404
    assert missing_run.json() == {"detail": "artifact not found"}
    assert missing_artifact.status_code == 404
    assert missing_artifact.json() == {"detail": "artifact not found"}


@pytest.mark.integration
@pytest.mark.parametrize(
    "violation",
    [
        "path_escape",
        "missing_file",
        "directory",
        "symlink_escape",
        "size_mismatch",
        "checksum_mismatch",
        "media_type",
        "invalid_utf8",
        "content_too_large",
    ],
)
async def test_artifact_content_rejects_unverified_content_without_leakage(
    database_engine: AsyncEngine,
    test_database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    violation: str,
) -> None:
    sentinel = "PRIVATE-REPORT-SENTINEL"
    content = f"{sentinel}\nreport body".encode()
    artifact_root = tmp_path / "artifacts"
    path = "reports/unverified/support-report.md"
    media_type = "text/markdown"
    recorded_size: int | None = None
    recorded_hash: str | None = None
    target = artifact_root / path
    simulated_symlink: tuple[Path, Path] | None = None

    if violation == "path_escape":
        path = "../outside.md"
        target = tmp_path / "outside.md"
    elif violation == "missing_file":
        target = artifact_root / path
    elif violation == "directory":
        target.mkdir(parents=True)
    elif violation == "symlink_escape":
        outside = tmp_path / "outside.md"
        outside.write_bytes(content)
        target.parent.mkdir(parents=True)
        try:
            target.symlink_to(outside)
        except OSError:
            target.write_bytes(b"simulated link")
            simulated_symlink = (target, outside.resolve(strict=True))
    elif violation == "size_mismatch":
        recorded_size = len(content) + 1
    elif violation == "checksum_mismatch":
        recorded_hash = "0" * 64
    elif violation == "media_type":
        media_type = "text/plain"
    elif violation == "invalid_utf8":
        content += b"\xff"
    elif violation == "content_too_large":
        content = sentinel.encode() + b"x" * (262_145 - len(sentinel))

    if violation not in {"missing_file", "directory", "symlink_escape"}:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    run_id = await create_artifact_run(
        database_engine,
        key=f"artifact-content-{violation}",
        path=path,
        content=content,
        sha256=recorded_hash,
        size_bytes=recorded_size,
        media_type=media_type,
    )
    app = create_app(settings_for(test_database_url, artifact_root=artifact_root))
    if simulated_symlink is not None:
        linked_path, resolved_outside = simulated_symlink
        original_resolve = Path.resolve

        def resolve_simulated_link(path_object: Path, strict: bool = False) -> Path:
            if path_object == linked_path:
                return resolved_outside
            return original_resolve(path_object, strict=strict)

        monkeypatch.setattr(Path, "resolve", resolve_simulated_link)

    with caplog.at_level("INFO"):
        async with api_client(app) as client:
            response = await client.get(f"/api/v1/runs/{run_id}/artifact")

    assert response.status_code == 409
    assert response.json() == {"detail": "artifact could not be verified"}
    assert sentinel not in response.text
    assert all(sentinel not in record.getMessage() for record in caplog.records)
