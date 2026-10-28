from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    ArtifactRecord,
    AttemptRecord,
    DeliveryRecord,
    RunRecord,
    StepRecord,
)
from agentforge.domain import ArtifactRef, AttemptState, FailureKind, RunState
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
        max_decisions=4,
        max_ticket_bytes=65_536,
        max_total_ticket_bytes=262_144,
        inbox_base_url="http://127.0.0.1:8001",
        approval_ttl_seconds=30,
    )


def test_delivery_recovery_uses_isolated_temporal_namespace(tmp_path: Path) -> None:
    settings = recovery_settings("postgresql+psycopg://test", tmp_path)

    assert settings.temporal_namespace == "agentforge-test"


def worker_environment(settings: Settings, marker: Path | None) -> dict[str, str]:
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
            "PYTHONUNBUFFERED": "1",
        }
    )
    if marker is None:
        environment.pop("AGENTFORGE_DELIVERY_COMMIT_MARKER", None)
    else:
        environment["AGENTFORGE_DELIVERY_COMMIT_MARKER"] = str(marker)
    return environment


def start_worker(settings: Settings, marker: Path | None) -> subprocess.Popen[bytes]:
    helper = Path(__file__).parents[1] / "helpers" / "delivery_worker.py"
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, str(helper)],
        cwd=Path(__file__).parents[2],
        env=worker_environment(settings, marker),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )


def start_inbox(database_url: str) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment.update(
        {
            "AGENTFORGE_INBOX_DATABASE_URL": database_url,
            "PYTHONUNBUFFERED": "1",
        }
    )
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from agentforge.inbox_api import main; main()",
        ],
        cwd=Path(__file__).parents[2],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )


async def stop_process(process: subprocess.Popen[bytes] | None) -> tuple[str, str]:
    if process is None:
        return "", ""
    if process.poll() is None:
        process.terminate()
    try:
        stdout, stderr = await asyncio.to_thread(process.communicate, timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = await asyncio.to_thread(process.communicate, timeout=5)
    return (
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def wait_for_inbox(
    process: subprocess.Popen[bytes], *, timeout: float = 15
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with httpx.AsyncClient() as client:
        while loop.time() < deadline:
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


async def wait_for_approval(
    client: httpx.AsyncClient,
    run_id: str,
    worker: subprocess.Popen[bytes],
    *,
    timeout: float = 15,
) -> dict[str, object]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if worker.poll() is not None:
            _, stderr = await stop_process(worker)
            pytest.fail("Worker exited before approval: " + stderr[-2_000:])
        response = await client.get(f"/api/v1/runs/{run_id}/approval")
        if response.status_code == 200:
            return response.json()
        await asyncio.sleep(0.05)
    pytest.fail("Workflow did not create an approval")


async def wait_for_marker(
    marker: Path, worker: subprocess.Popen[bytes], *, timeout: float = 20
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if marker.is_file():
            return
        if worker.poll() is not None:
            _, stderr = await stop_process(worker)
            pytest.fail("Worker exited before delivery commit: " + stderr[-2_000:])
        await asyncio.sleep(0.05)
    pytest.fail("Worker did not reach the post-commit checkpoint")


@asynccontextmanager
async def api_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client


@pytest.mark.integration
async def test_worker_death_after_inbox_commit_retries_one_operation(
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
    marker = tmp_path / "receiver-committed"
    inbox_process = start_inbox(inbox_test_database_url)
    first_worker: subprocess.Popen[bytes] | None = None
    replacement_worker: subprocess.Popen[bytes] | None = None
    diagnostics: list[str] = []
    try:
        await wait_for_inbox(inbox_process)
        first_worker = start_worker(settings, marker)
        async with api_client(app) as client:
            submitted = await client.post(
                "/api/v1/runs",
                json={
                    "submission_key": "delivery-recovery",
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

            approval = await wait_for_approval(client, run_id, first_worker)
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
            await wait_for_marker(marker, first_worker)

            stdout, stderr = await stop_process(first_worker)
            diagnostics.extend((stdout, stderr))
            replacement_worker = start_worker(settings, None)
            handle = temporal_client.get_workflow_handle(
                workflow_id, result_type=ArtifactRef
            )
            artifact = await asyncio.wait_for(handle.result(), timeout=45)
            history = await handle.fetch_history()
            async with httpx.AsyncClient() as receiver_client:
                receiver_response = await receiver_client.get(
                    f"{settings.inbox_base_url}/api/v1/deliveries/"
                    f"{approval['operation_id']}"
                )
            assert receiver_response.status_code == 200
            receiver_receipt = receiver_response.json()

        sessions = async_sessionmaker(database_engine, expire_on_commit=False)
        async with sessions() as session:
            run = await session.get(RunRecord, run_id)
            approvals = list((await session.scalars(select(ApprovalRecord))).all())
            delivery = (await session.scalars(select(DeliveryRecord))).one()
            artifact_record = (await session.scalars(select(ArtifactRecord))).one()
            delivery_step = (
                await session.scalars(
                    select(StepRecord).where(StepRecord.name == "deliver_report")
                )
            ).one()
            attempts = list(
                (
                    await session.scalars(
                        select(AttemptRecord)
                        .where(AttemptRecord.step_id == delivery_step.id)
                        .order_by(AttemptRecord.attempt_number)
                    )
                ).all()
            )
            run_count = await session.scalar(select(func.count()).select_from(RunRecord))

        inbox_sessions = async_sessionmaker(
            inbox_database_engine, expire_on_commit=False
        )
        async with inbox_sessions() as session:
            inbox_rows = list(
                (await session.scalars(select(InboxDeliveryRecord))).all()
            )

        assert run_count == 1
        assert run is not None and run.state == RunState.SUCCEEDED.value
        assert run.workflow_id == workflow_id
        assert len(approvals) == 1
        assert str(approvals[0].id) == approval["approval_id"]
        assert approvals[0].action_hash == approval["action_hash"]
        assert str(approvals[0].operation_id) == approval["operation_id"]
        assert delivery.operation_id == approvals[0].operation_id
        assert len(inbox_rows) == 1
        assert delivery.receiver_delivery_id == inbox_rows[0].receiver_delivery_id
        assert receiver_receipt["delivery_id"] == str(delivery.receiver_delivery_id)
        assert [attempt.attempt_number for attempt in attempts] == [1, 2]
        assert [attempt.state for attempt in attempts] == [
            AttemptState.FAILED.value,
            AttemptState.SUCCEEDED.value,
        ]
        assert [attempt.failure_kind for attempt in attempts] == [
            FailureKind.RETRYABLE.value,
            None,
        ]
        assert artifact_record.path == artifact.path
        assert artifact_record.sha256 == artifact.sha256
        assert artifact_record.size_bytes == artifact.size_bytes
        history_json = history.to_json()
        assert "Export remains pending" not in history_json
        assert "# Support Report" not in history_json
    finally:
        diagnostics.extend(await stop_process(first_worker))
        diagnostics.extend(await stop_process(replacement_worker))
        diagnostics.extend(await stop_process(inbox_process))

    assert first_worker is not None and first_worker.returncode is not None
    assert replacement_worker is not None and replacement_worker.returncode is not None
    assert inbox_process.returncode is not None
    assert "Export remains pending" not in "".join(diagnostics)
    assert "# Support Report" not in "".join(diagnostics)
