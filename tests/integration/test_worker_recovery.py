from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from agentforge.config import Settings
from agentforge.db_models import (
    AgentVersionRecord,
    ApprovalRecord,
    ArtifactRecord,
    AttemptRecord,
    RunRecord,
    StepRecord,
)
from agentforge.domain import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalSignal,
    ArtifactRef,
    AttemptState,
    FailureKind,
    RunState,
    SubmitRun,
)
from agentforge.relay import TemporalWorkflowStarter, relay_once
from agentforge.repository import ApprovalRepository, RunRepository, step_id_for


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
    )


def test_recovery_settings_use_isolated_temporal_namespace(tmp_path: Path) -> None:
    settings = recovery_settings("postgresql+psycopg://test", tmp_path)

    assert settings.temporal_namespace == "agentforge-test"


def worker_environment(
    settings: Settings, *, marker_path: Path | None = None
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
            "PYTHONUNBUFFERED": "1",
        }
    )
    if marker_path is not None:
        environment["AGENTFORGE_RECOVERY_BLOCK_MARKER"] = str(marker_path)
    else:
        environment.pop("AGENTFORGE_RECOVERY_BLOCK_MARKER", None)
    return environment


def start_worker_process(
    settings: Settings, *, marker_path: Path | None = None
) -> subprocess.Popen[bytes]:
    helper = Path(__file__).parents[1] / "helpers" / "recovery_worker.py"
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, str(helper)],
        cwd=Path(__file__).parents[2],
        env=worker_environment(settings, marker_path=marker_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )


async def stop_process(process: subprocess.Popen[bytes]) -> tuple[str, str]:
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


async def wait_for_marker(
    marker_path: Path, process: subprocess.Popen[bytes], *, timeout: float = 10
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if marker_path.is_file():
            return
        if process.poll() is not None:
            _, stderr = await stop_process(process)
            pytest.fail(
                "recovery Worker exited before its checkpoint; stderr tail: "
                + stderr[-2_000:]
            )
        await asyncio.sleep(0.05)
    _, stderr = await stop_process(process)
    pytest.fail(
        "recovery Worker did not reach its checkpoint; stderr tail: "
        + stderr[-2_000:]
    )


async def wait_for_approval(
    sessions: async_sessionmaker, run_id: UUID, *, timeout: float = 15
) -> ApprovalRecord:
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
    pytest.fail("replacement Worker did not create an approval")


@pytest.mark.integration
async def test_worker_termination_replays_and_completes_on_replacement(
    database_engine: AsyncEngine, test_database_url: str, tmp_path: Path
) -> None:
    settings = recovery_settings(test_database_url, tmp_path)
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
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
                submission_key="worker-recovery",
                goal="Find recurring support themes",
                ticket_files=("ticket-001.txt", "ticket-002.txt"),
            ),
            version_id,
        )

    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
    marker = tmp_path / "second-decision-running"
    first_worker = start_worker_process(settings, marker_path=marker)
    replacement_worker: subprocess.Popen[bytes] | None = None
    try:
        stats = await relay_once(settings, TemporalWorkflowStarter(client))
        assert stats.started == 1
        await wait_for_marker(marker, first_worker)

        await stop_process(first_worker)
        assert first_worker.returncode is not None

        replacement_worker = start_worker_process(settings)
        handle = client.get_workflow_handle(
            submitted.run.workflow_id, result_type=ArtifactRef
        )
        approval = await wait_for_approval(sessions, submitted.run.run_id)
        async with sessions() as session:
            await ApprovalRepository.decide(
                session,
                approval.id,
                ApprovalDecisionRequest(
                    action_hash=approval.action_hash,
                    decision=ApprovalDecision.APPROVE,
                ),
                now=datetime.now(UTC),
            )
        await handle.signal(
            "approval_decided",
            ApprovalSignal(
                message_id=uuid5(approval.id, "decision:APPROVE"),
                approval_id=approval.id,
                action_hash=approval.action_hash,
                decision=ApprovalDecision.APPROVE,
            ),
        )
        artifact = await asyncio.wait_for(handle.result(), timeout=45)
        history = await handle.fetch_history()

        async with sessions() as session:
            run = await session.get(RunRecord, submitted.run.run_id)
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
                        select(AttemptRecord)
                        .join(StepRecord)
                        .order_by(StepRecord.ordinal, AttemptRecord.attempt_number)
                    )
                ).all()
            )
            artifacts = list((await session.scalars(select(ArtifactRecord))).all())
            run_count = await session.scalar(select(func.count()).select_from(RunRecord))

        attempts_by_step: dict[object, list[AttemptRecord]] = {}
        for attempt in attempts:
            attempts_by_step.setdefault(attempt.step_id, []).append(attempt)
        retried_step = next(step for step in steps if step.ordinal == 1)
        decision_step = next(step for step in steps if step.ordinal == 0)
        retried_attempts = attempts_by_step[retried_step.id]
        decision_attempts = attempts_by_step[decision_step.id]
        history_events = history.to_json_dict()["events"]
        read_schedules = [
            event
            for event in history_events
            if event.get("activityTaskScheduledEventAttributes", {})
            .get("activityType", {})
            .get("name")
            == "read_tickets"
        ]
        retried_schedule_id = read_schedules[0]["eventId"]
        retry_starts = [
            event
            for event in history_events
            if event.get("activityTaskStartedEventAttributes", {}).get(
                "scheduledEventId"
            )
            == retried_schedule_id
        ]
        retry_started_id = retry_starts[0]["eventId"]
        retry_completions = [
            event
            for event in history_events
            if event.get("activityTaskCompletedEventAttributes", {}).get(
                "startedEventId"
            )
            == retry_started_id
        ]

        assert run_count == 1
        assert run is not None
        assert run.workflow_id == submitted.run.workflow_id
        assert run.state == RunState.SUCCEEDED.value
        assert len(retry_starts) == 1
        assert retry_starts[0]["activityTaskStartedEventAttributes"]["attempt"] == 2
        assert len(retry_completions) == 1
        assert retried_step.name == "read_tickets"
        assert retried_step.id == step_id_for(submitted.run.run_id, 1)
        assert [attempt.attempt_number for attempt in retried_attempts] == [1, 2]
        assert [attempt.state for attempt in retried_attempts] == [
            AttemptState.FAILED.value,
            AttemptState.SUCCEEDED.value,
        ]
        assert [attempt.failure_kind for attempt in retried_attempts] == [
            FailureKind.RETRYABLE.value,
            None,
        ]
        assert decision_step.name == "decide_action"
        assert len(decision_attempts) == 1
        assert len(artifacts) == 1
        report_bytes = (settings.artifact_root / artifact.path).read_bytes()
        assert artifacts[0].sha256 == hashlib.sha256(report_bytes).hexdigest()
        assert artifacts[0].size_bytes == len(report_bytes)
    finally:
        await stop_process(first_worker)
        if replacement_worker is not None:
            await stop_process(replacement_worker)

    assert first_worker.returncode is not None
    assert replacement_worker is not None
    assert replacement_worker.returncode is not None
