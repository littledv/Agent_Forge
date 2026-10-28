from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from agentforge.api import create_app
from agentforge.config import Settings
from agentforge.db_models import (
    ApprovalRecord,
    AttemptRecord,
    DeliveryRecord,
    RunRecord,
    StepRecord,
)
from agentforge.domain import ArtifactRef, AttemptState, RunState, StepState
from agentforge.inbox_db_models import InboxDeliveryRecord
from agentforge.relay import (
    TemporalWorkflowSignaler,
    TemporalWorkflowStarter,
    relay_once,
    relay_signals_once,
)


def recovery_settings(database_url: str, tmp_path: Path) -> Settings:
    tickets = tmp_path / "tickets"
    tickets.mkdir()
    (tickets / "ticket-001.txt").write_text(
        "PRIVATE-RECOVERY-A export remains pending.\n", encoding="utf-8"
    )
    (tickets / "ticket-002.txt").write_text(
        "PRIVATE-RECOVERY-B reset instructions are unclear.\n", encoding="utf-8"
    )
    return Settings(
        database_url=database_url,
        temporal_address="127.0.0.1:7233",
        temporal_namespace="agentforge-test",
        temporal_task_queue="agentforge-support-report-v1",
        ticket_root=tickets,
        artifact_root=tmp_path / "artifacts",
        max_decisions=4,
        max_ticket_bytes=65_536,
        max_total_ticket_bytes=262_144,
        inbox_base_url="http://127.0.0.1:8001",
        approval_ttl_seconds=30,
    )


def worker_environment(
    settings: Settings, *, marker: Path | None, identity_token: str | None
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "AGENTFORGE_DATABASE_URL": settings.database_url,
            "AGENTFORGE_TEMPORAL_ADDRESS": settings.temporal_address,
            "AGENTFORGE_TEMPORAL_NAMESPACE": settings.temporal_namespace,
            "AGENTFORGE_TEMPORAL_TASK_QUEUE": settings.temporal_task_queue,
            "AGENTFORGE_TICKET_ROOT": str(settings.ticket_root),
            "AGENTFORGE_ARTIFACT_ROOT": str(settings.artifact_root),
            "AGENTFORGE_MAX_DECISIONS": str(settings.max_decisions),
            "AGENTFORGE_MAX_TICKET_BYTES": str(settings.max_ticket_bytes),
            "AGENTFORGE_MAX_TOTAL_TICKET_BYTES": str(
                settings.max_total_ticket_bytes
            ),
            "AGENTFORGE_INBOX_BASE_URL": settings.inbox_base_url,
            "AGENTFORGE_APPROVAL_TTL_SECONDS": str(settings.approval_ttl_seconds),
            "AGENTFORGE_WORKER_MODEL_PROFILE": "mock-v1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    environment.pop("AGENTFORGE_REVIEW_BLOCK_MARKER", None)
    environment.pop("AGENTFORGE_RECOVERY_IDENTITY_TOKEN", None)
    if marker is not None and identity_token is not None:
        environment["AGENTFORGE_REVIEW_BLOCK_MARKER"] = str(marker)
        environment["AGENTFORGE_RECOVERY_IDENTITY_TOKEN"] = identity_token
    return environment


def start_worker(
    settings: Settings, *, marker: Path | None = None
) -> tuple[subprocess.Popen[bytes], str | None, datetime]:
    helper = Path(__file__).parents[1] / "helpers" / "multi_agent_recovery_worker.py"
    token = str(uuid4()) if marker is not None else None
    launched_at = datetime.now(UTC)
    process = subprocess.Popen(
        [sys.executable, str(helper)],
        cwd=Path(__file__).parents[2],
        env=worker_environment(settings, marker=marker, identity_token=token),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return process, token, launched_at


def start_inbox(database_url: str) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment.update(
        {
            "AGENTFORGE_INBOX_DATABASE_URL": database_url,
            "PYTHONUNBUFFERED": "1",
        }
    )
    return subprocess.Popen(
        [sys.executable, "-c", "from agentforge.inbox_api import main; main()"],
        cwd=Path(__file__).parents[2],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


async def stop_process(process: subprocess.Popen[bytes] | None) -> tuple[str, str]:
    if process is None:
        return "", ""
    if process.poll() is None:
        process.terminate()
    try:
        stdout, stderr = await asyncio.to_thread(process.communicate, timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = await asyncio.to_thread(process.communicate, timeout=8)
    return (
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def wait_for_inbox(process: subprocess.Popen[bytes]) -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(300):
            if process.poll() is not None:
                _, stderr = await stop_process(process)
                pytest.fail("inbox exited before readiness: " + stderr[-2_000:])
            try:
                response = await client.get("http://127.0.0.1:8001/health/ready")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
    pytest.fail("inbox did not become ready")


async def wait_for_review_marker(
    marker: Path,
    worker: subprocess.Popen[bytes],
    *,
    identity_token: str,
    launched_at: datetime,
) -> dict[str, object]:
    for _ in range(400):
        if worker.poll() is not None:
            _, stderr = await stop_process(worker)
            pytest.fail("blocking Worker exited before review: " + stderr[-2_000:])
        if marker.is_file():
            identity = json.loads(marker.read_text(encoding="utf-8"))
            assert isinstance(identity["pid"], int) and identity["pid"] > 0
            assert identity["identity_token"] == identity_token
            assert datetime.fromisoformat(str(identity["started_at_utc"])) >= launched_at
            return identity
        await asyncio.sleep(0.05)
    pytest.fail("blocking Worker did not reach review_report_r0")


def decode_history(history_json: str) -> str:
    values: list[str] = [history_json]

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "data" and isinstance(item, str):
                    try:
                        values.append(base64.b64decode(item).decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        pass
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(json.loads(history_json))
    return "\n".join(values)


@asynccontextmanager
async def api_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client


async def wait_for_approval(
    client: httpx.AsyncClient,
    run_id: str,
    worker: subprocess.Popen[bytes],
) -> dict[str, object]:
    for _ in range(500):
        if worker.poll() is not None:
            _, stderr = await stop_process(worker)
            pytest.fail("replacement Worker exited: " + stderr[-2_000:])
        response = await client.get(f"/api/v1/runs/{run_id}/approval")
        if response.status_code == 200:
            return response.json()
        await asyncio.sleep(0.05)
    pytest.fail("recovered Workflow did not request approval")


@pytest.mark.integration
async def test_review_worker_loss_retries_one_role_without_repeating_research(
    database_engine: AsyncEngine,
    inbox_database_engine: AsyncEngine,
    test_database_url: str,
    inbox_test_database_url: str,
    tmp_path: Path,
) -> None:
    settings = recovery_settings(test_database_url, tmp_path)
    app = create_app(settings)
    temporal_client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
    marker = tmp_path / "review-r0-started.json"
    inbox_process = start_inbox(inbox_test_database_url)
    first_worker: subprocess.Popen[bytes] | None = None
    replacement_worker: subprocess.Popen[bytes] | None = None
    first_identity: dict[str, object] | None = None
    diagnostics: list[str] = []
    try:
        await wait_for_inbox(inbox_process)
        first_worker, token, launched_at = start_worker(settings, marker=marker)
        assert token is not None
        async with api_client(app) as client:
            submitted = await client.post(
                "/api/v1/runs",
                json={
                    "submission_key": "multi-agent-review-recovery",
                    "goal": "Find recurring support themes",
                    "ticket_files": ["ticket-001.txt", "ticket-002.txt"],
                },
            )
            assert submitted.status_code == 202
            run_id = submitted.json()["run_id"]
            workflow_id = submitted.json()["workflow_id"]
            started = await relay_once(
                settings, TemporalWorkflowStarter(temporal_client)
            )
            assert started.started == 1

            first_identity = await wait_for_review_marker(
                marker,
                first_worker,
                identity_token=token,
                launched_at=launched_at,
            )
            replacement_worker, _, _ = start_worker(settings)
            await asyncio.sleep(0.5)
            assert first_worker.poll() is None
            assert json.loads(marker.read_text(encoding="utf-8")) == first_identity
            os.kill(int(first_identity["pid"]), signal.SIGTERM)
            first_stdout, first_stderr = await stop_process(first_worker)
            diagnostics.extend((first_stdout, first_stderr))

            approval = await wait_for_approval(client, run_id, replacement_worker)
            decided = await client.post(
                f"/api/v1/approvals/{approval['approval_id']}/decision",
                json={
                    "action_hash": approval["action_hash"],
                    "decision": "APPROVE",
                },
            )
            assert decided.status_code == 202
            signaled = await relay_signals_once(
                settings, TemporalWorkflowSignaler(temporal_client)
            )
            assert signaled.sent == 1
            handle = temporal_client.get_workflow_handle(
                workflow_id, result_type=ArtifactRef
            )
            artifact = await asyncio.wait_for(handle.result(), timeout=60)
            decoded_history = decode_history((await handle.fetch_history()).to_json())

        sessions = async_sessionmaker(database_engine, expire_on_commit=False)
        async with sessions() as session:
            run = await session.get(RunRecord, run_id)
            approval_rows = list(
                (await session.scalars(select(ApprovalRecord))).all()
            )
            delivery_rows = list(
                (await session.scalars(select(DeliveryRecord))).all()
            )
            steps = list(
                (
                    await session.scalars(
                        select(StepRecord).order_by(StepRecord.ordinal)
                    )
                ).all()
            )
            attempts = list(
                (
                    await session.scalars(
                        select(AttemptRecord).order_by(
                            AttemptRecord.step_id, AttemptRecord.attempt_number
                        )
                    )
                ).all()
            )
            run_count = await session.scalar(
                select(func.count()).select_from(RunRecord)
            )
        inbox_sessions = async_sessionmaker(
            inbox_database_engine, expire_on_commit=False
        )
        async with inbox_sessions() as session:
            inbox_rows = list(
                (await session.scalars(select(InboxDeliveryRecord))).all()
            )

        by_step = {
            step.name: [attempt for attempt in attempts if attempt.step_id == step.id]
            for step in steps
        }
        research_names = [
            step.name for step in steps if step.name.startswith("research_shard_")
        ]
        role_names = [
            step.name
            for step in steps
            if step.name.startswith(
                ("research_shard_", "write_report_", "review_report_")
            )
        ]
        review_attempts = by_step["review_report_r0"]

        assert run_count == 1
        assert run is not None and run.state == RunState.SUCCEEDED.value
        assert len(approval_rows) == 1
        assert len(delivery_rows) == 1
        assert len(inbox_rows) == 1
        assert (
            delivery_rows[0].receiver_delivery_id
            == inbox_rows[0].receiver_delivery_id
        )
        assert artifact.sha256 == delivery_rows[0].artifact_sha256
        assert research_names == ["research_shard_1", "research_shard_2"]
        assert all(
            len(by_step[name]) == 1
            and by_step[name][0].state == AttemptState.SUCCEEDED.value
            for name in research_names
        )
        assert [attempt.attempt_number for attempt in review_attempts] == [1, 2]
        assert review_attempts[-1].state == AttemptState.SUCCEEDED.value
        assert (
            next(step for step in steps if step.name == "review_report_r0").state
            == StepState.SUCCEEDED.value
        )
        assert len(role_names) <= 10
        assert not any(name.endswith("_r3") for name in role_names)

        database_evidence = "\n".join(
            [
                json.dumps(run.request_payload),
                *(json.dumps(row.action_payload) for row in approval_rows),
                *(step.name for step in steps),
            ]
        )
        for sentinel in (
            "PRIVATE-RECOVERY-A",
            "PRIVATE-RECOVERY-B",
            "# Support Report",
        ):
            assert sentinel not in database_evidence
            assert sentinel not in decoded_history
    finally:
        if first_identity is not None:
            try:
                os.kill(int(first_identity["pid"]), signal.SIGTERM)
            except OSError:
                pass
        diagnostics.extend(await stop_process(first_worker))
        diagnostics.extend(await stop_process(replacement_worker))
        diagnostics.extend(await stop_process(inbox_process))

    assert first_worker is not None and first_worker.returncode is not None
    assert replacement_worker is not None and replacement_worker.returncode is not None
    assert inbox_process.returncode is not None
    assert "PRIVATE-RECOVERY" not in "".join(diagnostics)
    assert "# Support Report" not in "".join(diagnostics)
