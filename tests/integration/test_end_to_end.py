from __future__ import annotations

import asyncio
import hashlib
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
import uvicorn

from agentforge import (
    AgentForgeClient,
    AgentForgeConflictError,
    AgentForgePollingTimeout,
    ApprovalDecision,
    RunState,
)
from agentforge.api import create_app
from agentforge.config import InboxSettings, Settings
from agentforge.db_models import DeliveryRecord, RunRecord, StepRecord
from agentforge.inbox_api import create_inbox_app
from agentforge.inbox_db_models import InboxDeliveryRecord
from agentforge.relay import (
    TemporalWorkflowSignaler,
    TemporalWorkflowStarter,
    relay_once,
    relay_signals_once,
)
from agentforge.worker import create_worker


def end_to_end_settings(
    database_url: str, tmp_path: Path, *, inbox_base_url: str
) -> Settings:
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
        inbox_base_url=inbox_base_url,
        approval_ttl_seconds=30,
    )


def test_end_to_end_uses_isolated_temporal_namespace(tmp_path: Path) -> None:
    settings = end_to_end_settings(
        "postgresql+psycopg://test",
        tmp_path,
        inbox_base_url="http://127.0.0.1:8001",
    )

    assert settings.temporal_namespace == "agentforge-test"


@asynccontextmanager
async def running_http_app(app: FastAPI) -> AsyncIterator[str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("HTTP app did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        listener.close()


@pytest.mark.integration
async def test_complete_submission_to_report_slice(
    database_engine: AsyncEngine,
    inbox_database_engine: AsyncEngine,
    test_database_url: str,
    inbox_test_database_url: str,
    tmp_path: Path,
) -> None:
    payload = {
        "submission_key": "end-to-end-001",
        "goal": "Find recurring support themes",
        "ticket_files": ["ticket-001.txt", "ticket-002.txt"],
    }

    inbox_app = create_inbox_app(
        InboxSettings(database_url=inbox_test_database_url)
    )
    async with running_http_app(inbox_app) as inbox_base_url:
        settings = end_to_end_settings(
            test_database_url, tmp_path, inbox_base_url=inbox_base_url
        )
        app = create_app(settings)
        worker = await create_worker(settings)
        temporal_client = await Client.connect(
            settings.temporal_address,
            namespace=settings.temporal_namespace,
            data_converter=pydantic_data_converter,
        )

        async with running_http_app(app) as api_base_url, worker:
            first_client = AgentForgeClient(api_base_url)
            try:
                submitted = await asyncio.to_thread(
                    first_client.submit_run, **payload
                )
                duplicate = await asyncio.to_thread(
                    first_client.submit_run, **payload
                )
                assert duplicate.run_id == submitted.run_id
                with pytest.raises(AgentForgeConflictError):
                    await asyncio.to_thread(
                        first_client.submit_run,
                        **{**payload, "goal": "Changed goal"},
                    )
                with pytest.raises(AgentForgePollingTimeout):
                    await asyncio.to_thread(
                        first_client.wait_for_run,
                        submitted.run_id,
                        timeout_seconds=0.1,
                        poll_interval_seconds=0.05,
                    )
            finally:
                first_client.close()

            run_id = submitted.run_id

            relay_stats = await relay_once(
                settings, TemporalWorkflowStarter(temporal_client)
            )
            assert relay_stats.started == 1

            with AgentForgeClient(api_base_url) as client:
                waiting = await asyncio.to_thread(
                    client.wait_for_run,
                    run_id,
                    states={RunState.WAITING_APPROVAL},
                    timeout_seconds=10,
                    poll_interval_seconds=0.05,
                )
                assert waiting.state == RunState.WAITING_APPROVAL

                approval = await asyncio.to_thread(client.get_approval, run_id)
                decided = await asyncio.to_thread(
                    client.decide_approval,
                    approval.approval_id,
                    action_hash=approval.action_hash,
                    decision=ApprovalDecision.APPROVE,
                )
                assert decided.approval_id == approval.approval_id
                signal_stats = await relay_signals_once(
                    settings, TemporalWorkflowSignaler(temporal_client)
                )
                assert signal_stats.sent == 1

                completed = await asyncio.to_thread(
                    client.wait_for_run,
                    run_id,
                    states={RunState.SUCCEEDED},
                    timeout_seconds=10,
                    poll_interval_seconds=0.05,
                )
                assert completed.state == RunState.SUCCEEDED
                artifact_content = await asyncio.to_thread(
                    client.get_artifact, run_id
                )

            assert completed.artifact is not None
            artifact = completed.artifact
            report = settings.artifact_root / artifact.path
            report_bytes = report.read_bytes()
            assert artifact.sha256 == hashlib.sha256(report_bytes).hexdigest()
            assert artifact.size_bytes == len(report_bytes)
            assert artifact_content.content == report_bytes.decode("utf-8")

        history = await temporal_client.get_workflow_handle(
            f"agentforge/run/{run_id}"
        ).fetch_history()

    inbox_sessions = async_sessionmaker(inbox_database_engine, expire_on_commit=False)
    async with inbox_sessions() as session:
        inbox_count = await session.scalar(
            select(func.count()).select_from(InboxDeliveryRecord)
        )
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        run_count = await session.scalar(
            select(func.count()).select_from(RunRecord)
        )
        delivery_count = await session.scalar(
            select(func.count()).select_from(DeliveryRecord)
        )
        step_names = list(
            (
                await session.scalars(
                    select(StepRecord.name).order_by(StepRecord.ordinal)
                )
            ).all()
        )
    role_names = [
        name
        for name in step_names
        if name.startswith(("research_shard_", "write_report_", "review_report_"))
    ]
    assert run_count == 1
    assert inbox_count == 1
    assert delivery_count == 1
    assert role_names == [
        "research_shard_1",
        "research_shard_2",
        "write_report_r0",
        "review_report_r0",
    ]
    assert len(role_names) <= 10
    history_json = history.to_json()
    assert "Export remains pending" not in history_json
    assert "# Support Report" not in history_json
