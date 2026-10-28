from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
import uvicorn

from agentforge import (
    AgentForgeClient,
    AgentForgeConflictError,
    AgentForgeNotFoundError,
    ApprovalDecision,
    RunState,
)
from agentforge.api import create_app
from agentforge.config import InboxSettings, Settings
from agentforge.db_models import (
    ApprovalRecord,
    ArtifactRecord,
    AttemptRecord,
    DeliveryRecord,
    ProjectionEventRecord,
    RunRecord,
    StepRecord,
    WorkflowSignalOutboxRecord,
    WorkflowStartOutboxRecord,
)
from agentforge.domain import ApprovalState, ArtifactRef, AttemptState, StepState
from agentforge.inbox_api import create_inbox_app
from agentforge.inbox_db_models import InboxDeliveryRecord
from agentforge.relay import (
    TemporalWorkflowSignaler,
    TemporalWorkflowStarter,
    relay_once,
    relay_signals_once,
)


PRIVATE_TICKET_PROBES = (
    "PRIVATE-PORTFOLIO-A export remains pending",
    "PRIVATE-PORTFOLIO-B reset instructions are unclear",
)
REPORT_BODY_PROBE = "# Support Report"
PRIVATE_PROBES = (*PRIVATE_TICKET_PROBES, REPORT_BODY_PROBE)


class EphemeralUvicorn:
    def __init__(self, *, exclude_ports: set[int] | None = None) -> None:
        excluded = exclude_ports or set()
        for _ in range(20):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
            if port not in excluded:
                break
            listener.close()
        else:  # pragma: no cover - OS would need to repeat excluded ports 20 times
            raise RuntimeError("could not reserve a distinct loopback port")
        listener.listen()
        self.listener = listener
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task[None] | None = None
        self.closed = False

    async def start(self, app: FastAPI, *, timeout: float = 8) -> None:
        assert self.server is None and self.task is None and not self.closed
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
                access_log=False,
            )
        )
        self.task = asyncio.create_task(
            self.server.serve(sockets=[self.listener]),
            name=f"uvicorn-{self.port}",
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.server.started:
                return
            if self.task.done():
                await self.task
                pytest.fail(f"loopback server on port {self.port} exited early")
            await asyncio.sleep(0.05)
        pytest.fail(f"loopback server on port {self.port} did not become ready")

    async def stop(self, *, timeout: float = 8) -> None:
        if self.closed:
            return
        try:
            if self.server is not None:
                self.server.should_exit = True
            if self.task is not None:
                await asyncio.wait_for(self.task, timeout=timeout)
        finally:
            self.listener.close()
            self.closed = True


@dataclass
class TrackedWorker:
    label: str
    process: subprocess.Popen[bytes]
    launched_at: datetime
    identity_token: str | None = None
    runtime_pid: int | None = None
    stdout: str = ""
    stderr: str = ""
    output_collected: bool = False


@dataclass(frozen=True)
class InboxReceiptSnapshot:
    operation_id: UUID
    request_hash: str
    action_hash: str
    artifact_path: str
    artifact_media_type: str
    artifact_size_bytes: int
    artifact_sha256: str
    receiver_delivery_id: UUID
    committed_at: datetime

    @classmethod
    def from_record(cls, record: InboxDeliveryRecord) -> InboxReceiptSnapshot:
        return cls(
            operation_id=record.operation_id,
            request_hash=record.request_hash,
            action_hash=record.action_hash,
            artifact_path=record.artifact_path,
            artifact_media_type=record.artifact_media_type,
            artifact_size_bytes=record.artifact_size_bytes,
            artifact_sha256=record.artifact_sha256,
            receiver_delivery_id=record.receiver_delivery_id,
            committed_at=record.committed_at,
        )


def portfolio_settings(
    database_url: str, tmp_path: Path, *, inbox_base_url: str
) -> Settings:
    tickets = tmp_path / "tickets"
    tickets.mkdir()
    (tickets / "ticket-001.txt").write_text(
        PRIVATE_TICKET_PROBES[0] + ".\n", encoding="utf-8"
    )
    (tickets / "ticket-002.txt").write_text(
        PRIVATE_TICKET_PROBES[1] + ".\n", encoding="utf-8"
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
        approval_ttl_seconds=60,
    )


def worker_environment(
    settings: Settings,
    *,
    review_marker: Path | None = None,
    identity_token: str | None = None,
    delivery_marker: Path | None = None,
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
            "AGENTFORGE_APPROVAL_TTL_SECONDS": str(
                settings.approval_ttl_seconds
            ),
            "AGENTFORGE_ENABLED_MODEL_PROFILES": "mock-v1",
            "AGENTFORGE_WORKER_MODEL_PROFILE": "mock-v1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    for name in (
        "AGENTFORGE_REVIEW_BLOCK_MARKER",
        "AGENTFORGE_RECOVERY_IDENTITY_TOKEN",
        "AGENTFORGE_DELIVERY_COMMIT_MARKER",
    ):
        environment.pop(name, None)
    if review_marker is not None:
        assert identity_token is not None
        environment["AGENTFORGE_REVIEW_BLOCK_MARKER"] = str(review_marker)
        environment["AGENTFORGE_RECOVERY_IDENTITY_TOKEN"] = identity_token
    if delivery_marker is not None:
        environment["AGENTFORGE_DELIVERY_COMMIT_MARKER"] = str(delivery_marker)
    return environment


def start_worker(
    settings: Settings,
    *,
    label: str,
    helper_name: str,
    review_marker: Path | None = None,
    identity_token: str | None = None,
    delivery_marker: Path | None = None,
) -> TrackedWorker:
    helper = Path(__file__).parents[1] / "helpers" / helper_name
    launched_at = datetime.now(UTC)
    process = subprocess.Popen(
        [sys.executable, str(helper)],
        cwd=Path(__file__).parents[2],
        env=worker_environment(
            settings,
            review_marker=review_marker,
            identity_token=identity_token,
            delivery_marker=delivery_marker,
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return TrackedWorker(
        label=label,
        process=process,
        launched_at=launched_at,
        identity_token=identity_token,
    )


async def stop_worker(worker: TrackedWorker | None) -> tuple[str, str]:
    if worker is None:
        return "", ""
    if worker.output_collected:
        return worker.stdout, worker.stderr
    if worker.runtime_pid is not None and worker.runtime_pid != worker.process.pid:
        try:
            os.kill(worker.runtime_pid, signal.SIGTERM)
        except OSError:
            pass
    if worker.process.poll() is None:
        worker.process.terminate()
    try:
        stdout, stderr = await asyncio.to_thread(
            worker.process.communicate, timeout=8
        )
    except subprocess.TimeoutExpired:
        worker.process.kill()
        try:
            stdout, stderr = await asyncio.to_thread(
                worker.process.communicate, timeout=8
            )
        except subprocess.TimeoutExpired as error:
            worker.process.kill()
            await asyncio.to_thread(worker.process.wait, timeout=2)
            raise RuntimeError(
                f"{worker.label} could not be reaped before the cleanup deadline"
            ) from error
    worker.stdout = stdout.decode("utf-8", errors="replace")
    worker.stderr = stderr.decode("utf-8", errors="replace")
    worker.output_collected = True
    return worker.stdout, worker.stderr


async def cleanup_portfolio_resources(
    *,
    workflow_handle: object | None,
    terminate_workflow: bool,
    clients: tuple[object | None, ...],
    workers: tuple[object, ...],
    servers: tuple[object, ...],
    diagnostics: list[str],
    timeout: float = 20,
) -> None:
    errors: list[Exception] = []

    def record_error(label: str, error: Exception) -> None:
        errors.append(RuntimeError(f"{label} cleanup failed: {type(error).__name__}"))

    if terminate_workflow and workflow_handle is not None:
        try:
            terminate = getattr(workflow_handle, "terminate")
            await asyncio.wait_for(
                terminate("portfolio test cleanup after incomplete proof"),
                timeout=timeout,
            )
        except Exception as error:
            record_error("workflow", error)

    for index, client in enumerate(clients, start=1):
        if client is None:
            continue
        try:
            getattr(client, "close")()
        except Exception as error:
            record_error(f"client {index}", error)

    for index, worker in enumerate(reversed(workers), start=1):
        try:
            stdout, stderr = await asyncio.wait_for(
                stop_worker(worker), timeout=timeout
            )
            diagnostics.extend((stdout, stderr))
        except Exception as error:
            record_error(f"worker {index}", error)

    for index, server in enumerate(reversed(servers), start=1):
        try:
            await asyncio.wait_for(
                getattr(server, "stop")(), timeout=timeout
            )
        except Exception as error:
            record_error(f"server {index}", error)

    if errors:
        raise ExceptionGroup("portfolio cleanup failures", errors)


def redacted_suffix(value: str, *, limit: int = 2_000) -> str:
    redacted = value
    for probe in PRIVATE_PROBES:
        redacted = redacted.replace(probe, "[REDACTED]")
    return redacted[-limit:]


async def fail_if_worker_exited(worker: TrackedWorker, context: str) -> None:
    if worker.process.poll() is None:
        return
    _, stderr = await stop_worker(worker)
    pytest.fail(
        f"{worker.label} exited during {context}: {redacted_suffix(stderr)}"
    )


async def wait_for_review_marker(
    marker: Path,
    worker: TrackedWorker,
    *,
    timeout: float = 25,
) -> dict[str, object]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await fail_if_worker_exited(worker, "review marker wait")
        if marker.is_file():
            identity = json.loads(marker.read_text(encoding="utf-8"))
            assert isinstance(identity["pid"], int) and identity["pid"] > 0
            assert identity["identity_token"] == worker.identity_token
            assert datetime.fromisoformat(str(identity["started_at_utc"])) >= (
                worker.launched_at
            )
            worker.runtime_pid = identity["pid"]
            return identity
        await asyncio.sleep(0.05)
    _, stderr = await stop_worker(worker)
    pytest.fail(
        "Worker A did not reach review_report_r0 before the deadline: "
        + redacted_suffix(stderr)
    )


async def wait_for_delivery_marker(
    marker: Path,
    worker: TrackedWorker,
    *,
    timeout: float = 25,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await fail_if_worker_exited(worker, "delivery marker wait")
        if marker.is_file():
            assert marker.read_text(encoding="utf-8") == "committed\n"
            assert marker.stat().st_mtime >= worker.launched_at.timestamp()
            return
        await asyncio.sleep(0.05)
    pytest.fail("Worker C did not reach the post-commit checkpoint")


async def wait_for_run_state(
    client: AgentForgeClient,
    run_id: UUID,
    state: RunState,
    *,
    worker: TrackedWorker,
    timeout: float,
) -> object:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await fail_if_worker_exited(worker, f"{state.value} wait")
        run = await asyncio.to_thread(client.get_run, run_id)
        if run.state is state:
            return run
        if run.state in {
            RunState.FAILED,
            RunState.DENIED,
            RunState.CANCELLED,
            RunState.TIMED_OUT,
            RunState.BUDGET_EXHAUSTED,
            RunState.NEEDS_ATTENTION,
        }:
            pytest.fail(f"run reached unexpected terminal state {run.state.value}")
        await asyncio.sleep(0.05)
    pytest.fail(f"run did not reach {state.value} before the deadline")


async def wait_for_approval(
    client: AgentForgeClient,
    run_id: UUID,
    *,
    worker: TrackedWorker,
    timeout: float = 30,
) -> object:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await fail_if_worker_exited(worker, "approval wait")
        try:
            return await asyncio.to_thread(client.get_approval, run_id)
        except AgentForgeNotFoundError:
            await asyncio.sleep(0.05)
    pytest.fail("recovered Workflow did not create an approval")


async def wait_for_inbox_row(
    engine: AsyncEngine,
    operation_id: UUID,
    *,
    worker: TrackedWorker,
    timeout: float = 10,
) -> InboxDeliveryRecord:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await fail_if_worker_exited(worker, "inbox commit wait")
        async with sessions() as session:
            row = await session.get(InboxDeliveryRecord, operation_id)
            if row is not None:
                return row
        await asyncio.sleep(0.05)
    pytest.fail("inbox did not commit the stable operation before the deadline")


async def assert_pre_review_steps_succeeded(engine: AsyncEngine) -> None:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        steps = list(
            (
                await session.scalars(
                    select(StepRecord).order_by(StepRecord.ordinal)
                )
            ).all()
        )
    by_name = {step.name: step for step in steps}
    assert by_name["research_shard_1"].state == StepState.SUCCEEDED.value
    assert by_name["research_shard_2"].state == StepState.SUCCEEDED.value
    assert by_name["write_report_r0"].state == StepState.SUCCEEDED.value


async def test_cleanup_attempts_every_owned_resource_after_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FailingHandle:
        async def terminate(self, _reason: str) -> None:
            events.append("workflow")
            raise TimeoutError("workflow cleanup failed")

    class Client:
        def __init__(self, label: str, *, fail: bool = False) -> None:
            self.label = label
            self.fail = fail

        def close(self) -> None:
            events.append(self.label)
            if self.fail:
                raise RuntimeError("client cleanup failed")

    class Worker:
        def __init__(self, label: str) -> None:
            self.label = label

    class Server:
        def __init__(self, label: str, *, fail: bool = False) -> None:
            self.label = label
            self.fail = fail

        async def stop(self) -> None:
            events.append(self.label)
            if self.fail:
                raise RuntimeError("server cleanup failed")

    async def fake_stop_worker(worker: Worker) -> tuple[str, str]:
        events.append(worker.label)
        if worker.label == "worker-2":
            raise RuntimeError("worker cleanup failed")
        return "stdout", "stderr"

    monkeypatch.setattr(sys.modules[__name__], "stop_worker", fake_stop_worker)
    diagnostics: list[str] = []
    with pytest.raises(ExceptionGroup) as exc_info:
        await cleanup_portfolio_resources(
            workflow_handle=FailingHandle(),
            terminate_workflow=True,
            clients=(Client("client-1", fail=True), Client("client-2")),
            workers=(Worker("worker-1"), Worker("worker-2")),
            servers=(Server("server-1", fail=True), Server("server-2")),
            diagnostics=diagnostics,
            timeout=0.2,
        )

    assert set(events) == {
        "workflow",
        "client-1",
        "client-2",
        "worker-1",
        "worker-2",
        "server-1",
        "server-2",
    }
    assert len(exc_info.value.exceptions) == 4
    assert diagnostics == ["stdout", "stderr"]


def decode_history(history_json: str) -> str:
    values = [history_json]

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


def assert_activity_retry_history(
    history_payload: dict[str, object], activity_name: str
) -> str:
    events = history_payload["events"]
    assert isinstance(events, list)
    scheduled = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("eventType") == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"
        and event.get("activityTaskScheduledEventAttributes", {})
        .get("activityType", {})
        .get("name")
        == activity_name
    ]
    assert len(scheduled) == 1
    scheduled_id = str(scheduled[0]["eventId"])
    related = [
        event
        for event in events
        if isinstance(event, dict)
        and any(
            str(attributes.get("scheduledEventId")) == scheduled_id
            for key, attributes in event.items()
            if key.endswith("EventAttributes") and isinstance(attributes, dict)
        )
    ]
    started_attempts = [
        int(event["activityTaskStartedEventAttributes"].get("attempt", 1))
        for event in related
        if "activityTaskStartedEventAttributes" in event
    ]
    assert started_attempts and max(started_attempts) == 2
    serialized = json.dumps(related, sort_keys=True).upper()
    assert "TIMEOUT" in serialized
    return str(
        scheduled[0]["activityTaskScheduledEventAttributes"]["activityId"]
    )


async def wait_for_port_release(port: int, *, timeout: float = 3) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.1)
        try:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        finally:
            probe.close()
        await asyncio.sleep(0.05)
    pytest.fail(f"ephemeral loopback listener {port} was not released")


@pytest.mark.integration
async def test_twenty_step_recovery_portfolio(
    database_engine: AsyncEngine,
    inbox_database_engine: AsyncEngine,
    test_database_url: str,
    inbox_test_database_url: str,
    tmp_path: Path,
) -> None:
    completed_steps: list[int] = []

    def complete(step: int) -> None:
        assert step == len(completed_steps) + 1
        completed_steps.append(step)

    workers: list[TrackedWorker] = []
    servers: list[EphemeralUvicorn] = []
    diagnostics: list[str] = []
    client_a: AgentForgeClient | None = None
    client_b: AgentForgeClient | None = None

    inbox_server = EphemeralUvicorn()
    servers.append(inbox_server)
    settings = portfolio_settings(
        test_database_url, tmp_path, inbox_base_url=inbox_server.base_url
    )
    assert settings.resolve_model_profile("mock-v1").profile_id == "mock-v1"
    assert settings.temporal_namespace == "agentforge-test"
    complete(1)

    api_a: EphemeralUvicorn | None = None
    history_json = ""
    submitted = None
    completed = None
    artifact_result: ArtifactRef | None = None
    artifact_content = None
    retained_approval = None
    committed_inbox_snapshot: InboxReceiptSnapshot | None = None
    workflow_handle = None
    try:
        await inbox_server.start(
            create_inbox_app(InboxSettings(database_url=inbox_test_database_url))
        )
        async with httpx.AsyncClient(trust_env=False, timeout=2) as health_client:
            health = await health_client.get(
                f"{inbox_server.base_url}/health/ready"
            )
        assert health.status_code == 200
        complete(2)

        review_marker = tmp_path / "review-r0-started.json"
        review_token = str(uuid4())
        worker_a = start_worker(
            settings,
            label="Worker A",
            helper_name="multi_agent_recovery_worker.py",
            review_marker=review_marker,
            identity_token=review_token,
        )
        workers.append(worker_a)
        assert worker_a.process.poll() is None
        complete(3)

        api_a = EphemeralUvicorn(exclude_ports={inbox_server.port})
        servers.append(api_a)
        await api_a.start(create_app(settings))
        client_a = AgentForgeClient(api_a.base_url, timeout_seconds=3)
        assert api_a.port != inbox_server.port
        complete(4)

        submission = {
            "submission_key": "milestone-8-continuous-recovery",
            "goal": "Find recurring support themes",
            "ticket_files": ["ticket-001.txt", "ticket-002.txt"],
        }
        submitted = await asyncio.to_thread(client_a.submit_run, **submission)
        run_id = submitted.run_id
        workflow_id = submitted.workflow_id
        assert workflow_id == f"agentforge/run/{run_id}"
        complete(5)

        duplicate = await asyncio.to_thread(client_a.submit_run, **submission)
        assert duplicate.run_id == run_id
        assert duplicate.workflow_id == workflow_id
        with pytest.raises(AgentForgeConflictError):
            await asyncio.to_thread(
                client_a.submit_run,
                **{**submission, "goal": "Changed support-report goal"},
            )
        complete(6)

        temporal_client = await Client.connect(
            settings.temporal_address,
            namespace=settings.temporal_namespace,
            data_converter=pydantic_data_converter,
        )
        started = await relay_once(
            settings, TemporalWorkflowStarter(temporal_client)
        )
        assert started.started == 1
        assert started.failed == 0
        workflow_handle = temporal_client.get_workflow_handle(
            workflow_id, result_type=ArtifactRef
        )
        complete(7)

        marker_identity = await wait_for_review_marker(review_marker, worker_a)
        await assert_pre_review_steps_succeeded(database_engine)
        complete(8)

        worker_b = start_worker(
            settings,
            label="Worker B",
            helper_name="multi_agent_recovery_worker.py",
        )
        workers.append(worker_b)
        await asyncio.sleep(0.5)
        assert worker_b.process.poll() is None
        assert marker_identity["pid"] == worker_a.runtime_pid
        await stop_worker(worker_a)
        assert worker_a.process.returncode is not None
        complete(9)

        waiting = await wait_for_run_state(
            client_a,
            run_id,
            RunState.WAITING_APPROVAL,
            worker=worker_b,
            timeout=35,
        )
        retained_approval = await wait_for_approval(
            client_a, run_id, worker=worker_b
        )
        assert waiting.state is RunState.WAITING_APPROVAL
        assert retained_approval.state is ApprovalState.PENDING
        complete(10)

        retained_approval_id = retained_approval.approval_id
        retained_operation_id = retained_approval.operation_id
        retained_action_hash = retained_approval.action_hash
        retained_artifact = retained_approval.action.artifact
        assert waiting.artifact == retained_artifact
        complete(11)

        client_a.close()
        client_a = None
        await api_a.stop()
        complete(12)

        api_b = EphemeralUvicorn(
            exclude_ports={inbox_server.port, api_a.port}
        )
        servers.append(api_b)
        await api_b.start(create_app(settings))
        client_b = AgentForgeClient(api_b.base_url, timeout_seconds=3)
        restored_run = await asyncio.to_thread(client_b.get_run, run_id)
        restored_approval = await asyncio.to_thread(client_b.get_approval, run_id)
        assert api_b.port != api_a.port
        assert restored_run.run_id == run_id
        assert restored_run.workflow_id == workflow_id
        assert restored_run.state is RunState.WAITING_APPROVAL
        assert restored_approval.approval_id == retained_approval_id
        assert restored_approval.operation_id == retained_operation_id
        assert restored_approval.action_hash == retained_action_hash
        assert restored_approval.action.artifact == retained_artifact
        complete(13)

        await stop_worker(worker_b)
        assert worker_b.process.returncode is not None
        complete(14)

        delivery_marker = tmp_path / "receiver-committed"
        worker_c = start_worker(
            settings,
            label="Worker C",
            helper_name="delivery_worker.py",
            delivery_marker=delivery_marker,
        )
        workers.append(worker_c)
        assert not delivery_marker.exists()
        assert worker_c.process.poll() is None
        complete(15)

        decided = await asyncio.to_thread(
            client_b.decide_approval,
            retained_approval_id,
            action_hash=retained_action_hash,
            decision=ApprovalDecision.APPROVE,
        )
        assert decided.approval_id == retained_approval_id
        assert decided.action_hash == retained_action_hash
        signaled = await relay_signals_once(
            settings, TemporalWorkflowSignaler(temporal_client)
        )
        assert signaled.sent == 1
        assert signaled.failed == 0
        complete(16)

        await wait_for_delivery_marker(delivery_marker, worker_c)
        committed_inbox_row = await wait_for_inbox_row(
            inbox_database_engine,
            retained_operation_id,
            worker=worker_c,
        )
        assert committed_inbox_row.operation_id == retained_operation_id
        committed_inbox_snapshot = InboxReceiptSnapshot.from_record(
            committed_inbox_row
        )
        await stop_worker(worker_c)
        assert worker_c.process.returncode is not None
        complete(17)

        worker_d = start_worker(
            settings,
            label="Worker D",
            helper_name="delivery_worker.py",
        )
        workers.append(worker_d)
        assert worker_d.process.poll() is None
        complete(18)

        assert workflow_handle is not None
        artifact_result = await asyncio.wait_for(
            workflow_handle.result(), timeout=35
        )
        completed = await wait_for_run_state(
            client_b,
            run_id,
            RunState.SUCCEEDED,
            worker=worker_d,
            timeout=10,
        )
        artifact_content = await asyncio.to_thread(client_b.get_artifact, run_id)
        report_path = settings.artifact_root / artifact_result.path
        report_bytes = report_path.read_bytes()
        assert completed.state is RunState.SUCCEEDED
        assert completed.run_id == run_id
        assert artifact_result.sha256 == hashlib.sha256(report_bytes).hexdigest()
        assert artifact_result.size_bytes == len(report_bytes)
        assert artifact_content.content.encode("utf-8") == report_bytes
        history_json = (await workflow_handle.fetch_history()).to_json()
        complete(19)
    finally:
        await cleanup_portfolio_resources(
            workflow_handle=workflow_handle,
            terminate_workflow=artifact_result is None,
            clients=(client_a, client_b),
            workers=tuple(workers),
            servers=tuple(servers),
            diagnostics=diagnostics,
        )

    assert submitted is not None
    assert completed is not None
    assert artifact_result is not None
    assert artifact_content is not None
    assert retained_approval is not None
    assert committed_inbox_snapshot is not None

    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    async with sessions() as session:
        runs = list((await session.scalars(select(RunRecord))).all())
        approvals = list((await session.scalars(select(ApprovalRecord))).all())
        deliveries = list((await session.scalars(select(DeliveryRecord))).all())
        artifacts = list((await session.scalars(select(ArtifactRecord))).all())
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
                        AttemptRecord.step_id,
                        AttemptRecord.attempt_number,
                    )
                )
            ).all()
        )
        start_outbox = list(
            (await session.scalars(select(WorkflowStartOutboxRecord))).all()
        )
        signal_outbox = list(
            (await session.scalars(select(WorkflowSignalOutboxRecord))).all()
        )
        projections = list(
            (await session.scalars(select(ProjectionEventRecord))).all()
        )
    inbox_sessions = async_sessionmaker(
        inbox_database_engine, expire_on_commit=False
    )
    async with inbox_sessions() as session:
        inbox_rows = list(
            (await session.scalars(select(InboxDeliveryRecord))).all()
        )

    assert len(runs) == 1
    run = runs[0]
    assert run.id == submitted.run_id
    assert run.workflow_id == submitted.workflow_id
    assert run.state == RunState.SUCCEEDED.value
    assert len(approvals) == 1
    assert approvals[0].id == retained_approval.approval_id
    assert approvals[0].operation_id == retained_approval.operation_id
    assert approvals[0].action_hash == retained_approval.action_hash
    assert approvals[0].state == ApprovalState.APPROVED.value
    assert len(deliveries) == 1
    assert deliveries[0].operation_id == retained_approval.operation_id
    assert len(inbox_rows) == 1
    assert inbox_rows[0].operation_id == retained_approval.operation_id
    assert InboxReceiptSnapshot.from_record(inbox_rows[0]) == committed_inbox_snapshot
    assert deliveries[0].receiver_delivery_id == (
        committed_inbox_snapshot.receiver_delivery_id
    )
    assert deliveries[0].committed_at == committed_inbox_snapshot.committed_at
    assert deliveries[0].artifact_sha256 == committed_inbox_snapshot.artifact_sha256
    assert len(artifacts) == 1
    assert artifacts[0].path == artifact_result.path
    assert artifacts[0].sha256 == artifact_result.sha256
    assert artifacts[0].size_bytes == artifact_result.size_bytes
    assert artifact_content.artifact.path == artifacts[0].path
    assert artifact_content.artifact.sha256 == artifacts[0].sha256
    assert artifact_content.artifact.size_bytes == artifacts[0].size_bytes

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
    assert research_names == ["research_shard_1", "research_shard_2"]
    assert role_names == [
        "research_shard_1",
        "research_shard_2",
        "write_report_r0",
        "review_report_r0",
    ]
    assert len(role_names) <= 10
    assert all(
        len(by_step[name]) == 1
        and by_step[name][0].attempt_number == 1
        and by_step[name][0].state == AttemptState.SUCCEEDED.value
        for name in research_names
    )
    assert len(by_step["write_report_r0"]) == 1
    assert by_step["write_report_r0"][0].attempt_number == 1
    assert by_step["write_report_r0"][0].state == AttemptState.SUCCEEDED.value
    assert [
        attempt.attempt_number for attempt in by_step["review_report_r0"]
    ] == [1, 2]
    assert by_step["review_report_r0"][-1].state == AttemptState.SUCCEEDED.value
    assert [
        attempt.attempt_number for attempt in by_step["deliver_report"]
    ] == [1, 2]
    assert by_step["deliver_report"][-1].state == AttemptState.SUCCEEDED.value
    assert len(start_outbox) == 1 and start_outbox[0].started_at is not None
    assert len(signal_outbox) == 1 and signal_outbox[0].sent_at is not None

    parsed_history = json.loads(history_json)
    workflow_starts = [
        event
        for event in parsed_history["events"]
        if event["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"
    ]
    assert len(workflow_starts) == 1
    review_activity_id = assert_activity_retry_history(
        parsed_history, "review_report_role"
    )
    delivery_activity_id = assert_activity_retry_history(
        parsed_history, "deliver_report"
    )
    assert review_activity_id != delivery_activity_id

    database_evidence = json.dumps(
        {
            "request": run.request_payload,
            "approval": approvals[0].action_payload,
            "projections": [row.payload for row in projections],
            "steps": [step.name for step in steps],
        },
        sort_keys=True,
    )
    decoded_history = decode_history(history_json)
    diagnostics_text = "\n".join(diagnostics)
    for probe in PRIVATE_PROBES:
        assert probe not in database_evidence
        assert probe not in decoded_history
        assert probe not in diagnostics_text

    assert all(worker.process.returncode is not None for worker in workers)
    for server in servers:
        assert server.closed
        await wait_for_port_release(server.port)

    complete(20)
    assert completed_steps == list(range(1, 21))
