from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from collections.abc import Callable
import hashlib
import inspect
import json
import subprocess
import sys
from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError
import pytest

import agentforge
import agentforge.client as client_module
from agentforge.client import AgentForgeClient
from agentforge.client_errors import (
    AgentForgeClientError,
    AgentForgeConflictError,
    AgentForgeNotFoundError,
    AgentForgePollingTimeout,
    AgentForgeProtocolError,
    AgentForgeRequestTimeout,
    AgentForgeRunTerminated,
    AgentForgeTransportError,
    AgentForgeUnavailableError,
    AgentForgeValidationError,
)
from agentforge.client_models import (
    Approval,
    Cancellation,
    ModelProfile,
    Run,
    RunDetail,
    RunListItem,
    TERMINAL_RUN_STATES,
)
from agentforge.domain import ApprovalDecision, AttemptState, RunState, StepState


RUN_ID = "11111111-1111-1111-1111-111111111111"


def _run_payload(*, state: str = "RUNNING") -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "workflow_id": f"agentforge/run/{RUN_ID}",
        "state": state,
        "created_at": "2026-10-27T08:00:00Z",
        "updated_at": "2026-10-27T08:00:01Z",
        "failure_kind": None,
        "artifact": None,
    }


def _run_detail_payload() -> dict[str, object]:
    run = {**_run_payload(), "future_run_field": "allowed"}
    attempt = {
        "attempt_number": 1,
        "state": "RUNNING",
        "failure_kind": None,
        "created_at": "2026-10-27T08:00:00Z",
        "updated_at": "2026-10-27T08:00:01Z",
        "model_provider": "mock",
        "model_name": "deterministic-support-report-v1",
        "model_digest": None,
        "model_request_count": 1,
        "model_input_tokens": 20,
        "model_output_tokens": 10,
        "model_duration_ms": 5,
        "provider_cost_estimate_usd": 0.0,
        "input_tokens_estimated": True,
        "usage_uncertain": False,
        "future_attempt_field": {"value": 1},
    }
    step = {
        "ordinal": 0,
        "name": "read_tickets",
        "state": "RUNNING",
        "created_at": "2026-10-27T08:00:00Z",
        "updated_at": "2026-10-27T08:00:01Z",
        "attempts": [attempt],
        "future_step_field": True,
    }
    model_profile = {
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
        "future_profile_field": "allowed",
    }
    return {
        "run": run,
        "goal": "Summarize support tickets",
        "ticket_files": ["ticket-001.txt"],
        "model_profile": model_profile,
        "projection_sequence": 2,
        "steps": [step],
        "approval": None,
        "cancellation_requested": False,
        "delivery": None,
        "future_detail_field": ["allowed"],
    }


def _approval_payload() -> dict[str, object]:
    return {
        "approval_id": "22222222-2222-2222-2222-222222222222",
        "run_id": RUN_ID,
        "operation_id": "33333333-3333-3333-3333-333333333333",
        "action_hash": "a" * 64,
        "action": {
            "type": "deliver_report",
            "target": "simulated-inbox",
            "artifact": {
                "path": f"reports/{RUN_ID}/support-report.md",
                "sha256": "b" * 64,
                "size_bytes": 20,
                "media_type": "text/markdown",
            },
        },
        "state": "PENDING",
        "expires_at": "2026-10-27T08:05:00Z",
        "created_at": "2026-10-27T08:00:00Z",
        "decided_at": None,
    }


class _CloseTrackingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.close_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_run_payload(), request=request)

    def close(self) -> None:
        self.close_count += 1


def _recording_transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    return httpx.MockTransport(record), requests


class _FakeClock:
    def __init__(self, current: float = 100.0) -> None:
        self.current = current
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


def test_client_models_parse_json_types_and_ignore_additive_nested_fields() -> None:
    detail = RunDetail.model_validate_json(json.dumps(_run_detail_payload()))

    assert detail.run.run_id == UUID(RUN_ID)
    assert isinstance(detail.run.created_at, datetime)
    assert detail.run.state is RunState.RUNNING
    assert detail.ticket_files == ("ticket-001.txt",)
    assert detail.model_profile.profile_id == "mock-v1"
    assert isinstance(detail.steps, tuple)
    assert detail.steps[0].state is StepState.RUNNING
    assert detail.steps[0].attempts[0].state is AttemptState.RUNNING
    assert detail.steps[0].attempts[0].model_request_count == 1


def test_client_models_reject_missing_or_invalid_known_fields() -> None:
    missing = _run_detail_payload()
    run = missing["run"]
    assert isinstance(run, dict)
    del run["run_id"]

    invalid = deepcopy(_run_detail_payload())
    invalid_run = invalid["run"]
    assert isinstance(invalid_run, dict)
    invalid_run["updated_at"] = 123

    with pytest.raises(ValidationError):
        RunDetail.model_validate_json(json.dumps(missing))
    with pytest.raises(ValidationError):
        RunDetail.model_validate_json(json.dumps(invalid))


def test_terminal_run_states_contains_every_terminal_state_only() -> None:
    assert TERMINAL_RUN_STATES == frozenset(
        {
            RunState.SUCCEEDED,
            RunState.FAILED,
            RunState.DENIED,
            RunState.CANCELLED,
            RunState.TIMED_OUT,
            RunState.BUDGET_EXHAUSTED,
            RunState.NEEDS_ATTENTION,
        }
    )
    assert RunState.RUNNING not in TERMINAL_RUN_STATES
    assert RunState.WAITING_APPROVAL not in TERMINAL_RUN_STATES


def test_client_errors_preserve_safe_context_without_body_or_ticket_content() -> None:
    error = AgentForgeConflictError(
        "submit_run",
        status_code=409,
        detail="submission key conflicts with durable state",
    )

    assert error.operation == "submit_run"
    assert error.status_code == 409
    assert error.detail == "submission key conflicts with durable state"
    assert "submit_run" in str(error)
    assert "409" in str(error)
    assert "submission key conflicts with durable state" in str(error)
    assert not hasattr(error, "response_body")
    assert "private ticket probe" not in repr(error)
    assert "response_body" not in inspect.signature(AgentForgeClientError).parameters
    with pytest.raises(TypeError):
        AgentForgeClientError(  # type: ignore[call-arg]
            "submit_run", response_body="private ticket probe"
        )


def test_polling_errors_retain_run_id_and_last_validated_run() -> None:
    run = Run.model_validate_json(
        json.dumps(_run_payload(state="FAILED"))
    )
    run_id = UUID(RUN_ID)

    timeout = AgentForgePollingTimeout(
        "wait_for_run", run_id=run_id, last_run=run
    )
    terminated = AgentForgeRunTerminated(
        "wait_for_run", run_id=run_id, last_run=run
    )

    assert timeout.run_id == run_id
    assert timeout.last_run is run
    assert terminated.run_id == run_id
    assert terminated.last_run is run
    assert "FAILED" in str(terminated)


def test_client_owns_one_transport_and_close_is_idempotent() -> None:
    transport = _CloseTrackingTransport()
    client = AgentForgeClient(transport=transport)

    client.close()
    client.close()

    assert transport.close_count == 1
    assert "client" not in inspect.signature(AgentForgeClient).parameters


def test_client_context_manager_closes_owned_http_client() -> None:
    transport = _CloseTrackingTransport()

    with AgentForgeClient(transport=transport) as client:
        assert client.get_run(RUN_ID).run_id == UUID(RUN_ID)

    assert transport.close_count == 1


def test_client_defaults_disable_environment_proxy_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class SpyClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def close(self) -> None:
            return

    monkeypatch.setattr("agentforge.client.httpx.Client", SpyClient)

    AgentForgeClient("http://127.0.0.1:8000/").close()

    assert captured["base_url"] == "http://127.0.0.1:8000"
    assert captured["timeout"] == 10.0
    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
    assert captured["transport"] is None


@pytest.mark.parametrize("timeout_seconds", [0, -1, float("nan"), float("inf")])
def test_client_rejects_non_positive_request_timeout_before_network(
    timeout_seconds: float,
) -> None:
    transport, requests = _recording_transport(
        lambda request: httpx.Response(200, json=[], request=request)
    )

    with pytest.raises(AgentForgeValidationError):
        AgentForgeClient(timeout_seconds=timeout_seconds, transport=transport)

    assert requests == []


def test_list_model_profiles_returns_typed_tuple() -> None:
    transport, requests = _recording_transport(
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "profile_id": "mock-v1",
                    "display_name": "Deterministic mock",
                    "provider": "mock",
                    "model_name": "deterministic-support-report-v1",
                    "model_digest": None,
                }
            ],
            request=request,
        )
    )

    with AgentForgeClient(transport=transport) as client:
        profiles = client.list_model_profiles()

    assert isinstance(profiles, tuple)
    assert isinstance(profiles[0], ModelProfile)
    assert profiles[0].profile_id == "mock-v1"
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/v1/model-profiles"


@pytest.mark.parametrize("status_code", [200, 202])
def test_submit_run_sends_validated_json_and_accepts_200_or_202(
    status_code: int,
) -> None:
    transport, requests = _recording_transport(
        lambda request: httpx.Response(
            status_code, json=_run_payload(), request=request
        )
    )

    with AgentForgeClient(transport=transport) as client:
        run = client.submit_run(
            submission_key="sdk-submit-001",
            goal="Summarize tickets",
            ticket_files=["ticket-001.txt"],
        )

    assert isinstance(run, Run)
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/api/v1/runs"
    assert json.loads(requests[0].content) == {
        "submission_key": "sdk-submit-001",
        "goal": "Summarize tickets",
        "ticket_files": ["ticket-001.txt"],
        "model_profile_id": "mock-v1",
    }


def test_list_runs_validates_limit_locally_and_sends_query() -> None:
    payload = [{**_run_payload(), "goal": "Summarize tickets"}]
    transport, requests = _recording_transport(
        lambda request: httpx.Response(200, json=payload, request=request)
    )

    with AgentForgeClient(transport=transport) as client:
        runs = client.list_runs(limit=7)
        with pytest.raises(AgentForgeValidationError):
            client.list_runs(limit=0)
        with pytest.raises(AgentForgeValidationError):
            client.list_runs(limit=51)

    assert isinstance(runs, tuple)
    assert isinstance(runs[0], RunListItem)
    assert requests[0].url.params["limit"] == "7"
    assert len(requests) == 1


def test_get_run_and_detail_use_canonical_uuid_paths() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/detail"):
            return httpx.Response(200, json=_run_detail_payload(), request=request)
        return httpx.Response(200, json=_run_payload(), request=request)

    transport, requests = _recording_transport(handler)
    uppercase_id = RUN_ID.upper()

    with AgentForgeClient(transport=transport) as client:
        run = client.get_run(uppercase_id)
        detail = client.get_run_detail(UUID(RUN_ID))

    assert isinstance(run, Run)
    assert isinstance(detail, RunDetail)
    assert [request.url.path for request in requests] == [
        f"/api/v1/runs/{RUN_ID}",
        f"/api/v1/runs/{RUN_ID}/detail",
    ]


def test_get_approval_and_decide_approval_use_exact_hash_and_decision() -> None:
    transport, requests = _recording_transport(
        lambda request: httpx.Response(200, json=_approval_payload(), request=request)
    )

    with AgentForgeClient(transport=transport) as client:
        approval = client.get_approval(RUN_ID)
        decided = client.decide_approval(
            approval.approval_id,
            action_hash=approval.action_hash,
            decision=ApprovalDecision.APPROVE,
        )

    assert isinstance(approval, Approval)
    assert decided.approval_id == approval.approval_id
    assert requests[0].url.path == f"/api/v1/runs/{RUN_ID}/approval"
    assert requests[1].url.path == (
        f"/api/v1/approvals/{approval.approval_id}/decision"
    )
    assert json.loads(requests[1].content) == {
        "action_hash": "a" * 64,
        "decision": "APPROVE",
    }


@pytest.mark.parametrize("status_code", [200, 202])
def test_cancel_run_accepts_200_or_202(status_code: int) -> None:
    payload = {
        "request_id": "44444444-4444-4444-4444-444444444444",
        "run_id": RUN_ID,
        "created_at": "2026-10-27T08:00:00Z",
    }
    transport, requests = _recording_transport(
        lambda request: httpx.Response(status_code, json=payload, request=request)
    )

    with AgentForgeClient(transport=transport) as client:
        cancellation = client.cancel_run(RUN_ID)

    assert isinstance(cancellation, Cancellation)
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"/api/v1/runs/{RUN_ID}/cancel"


@pytest.mark.parametrize(
    "operation",
    [
        lambda client: client.submit_run(
            submission_key="bad key",
            goal="Summarize",
            ticket_files=["ticket-001.txt"],
        ),
        lambda client: client.submit_run(
            submission_key="sdk-valid",
            goal="Summarize",
            ticket_files=[],
        ),
        lambda client: client.submit_run(
            submission_key="sdk-valid",
            goal="Summarize",
            ticket_files=["C:/private/ticket.txt"],
        ),
        lambda client: client.get_run("not-a-uuid"),
        lambda client: client.decide_approval(
            RUN_ID, action_hash="wrong", decision="MAYBE"
        ),
    ],
)
def test_invalid_inputs_fail_before_network(
    operation: Callable[[AgentForgeClient], object],
) -> None:
    transport, requests = _recording_transport(
        lambda request: httpx.Response(500, request=request)
    )

    with AgentForgeClient(transport=transport) as client:
        with pytest.raises(AgentForgeValidationError):
            operation(client)

    assert requests == []


@pytest.mark.parametrize(
    ("status_code", "body", "error_type", "detail"),
    [
        (404, {"detail": "run not found"}, AgentForgeNotFoundError, "run not found"),
        (409, {"detail": "conflict"}, AgentForgeConflictError, "conflict"),
        (422, {"detail": "invalid request"}, AgentForgeValidationError, "invalid request"),
        (
            422,
            {"detail": [{"input": "private ticket probe", "msg": "bad"}]},
            AgentForgeValidationError,
            "request validation failed",
        ),
        (503, {"detail": "service unavailable"}, AgentForgeUnavailableError, "service unavailable"),
    ],
)
def test_http_errors_map_to_sanitized_public_errors(
    status_code: int,
    body: dict[str, object],
    error_type: type[AgentForgeClientError],
    detail: str,
) -> None:
    transport, _ = _recording_transport(
        lambda request: httpx.Response(status_code, json=body, request=request)
    )

    with AgentForgeClient(transport=transport) as client:
        with pytest.raises(error_type) as captured:
            client.get_run(RUN_ID)

    assert captured.value.status_code == status_code
    assert captured.value.detail == detail
    assert "private ticket probe" not in str(captured.value)
    assert "private ticket probe" not in repr(captured.value)
    assert "private ticket probe" not in repr(vars(captured.value))


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (lambda request: httpx.Response(200, content=b"not-json", request=request), "invalid response"),
        (
            lambda request: httpx.Response(
                200, json={**_run_payload(), "state": "FUTURE"}, request=request
            ),
            "invalid response",
        ),
        (
            lambda request: httpx.Response(
                302,
                headers={"Location": "https://example.com/private"},
                request=request,
            ),
            "unexpected HTTP status",
        ),
        (lambda request: httpx.Response(500, json={"detail": "internal"}, request=request), "unexpected HTTP status"),
    ],
)
def test_unexpected_or_invalid_responses_raise_protocol_error(
    response: Callable[[httpx.Request], httpx.Response], detail: str
) -> None:
    transport, requests = _recording_transport(response)

    with AgentForgeClient(transport=transport) as client:
        with pytest.raises(AgentForgeProtocolError) as captured:
            client.get_run(RUN_ID)

    assert detail in str(captured.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("operation", "expected_path", "expected_body"),
    [
        (
            lambda client: client.submit_run(
                submission_key="sdk-ambiguous-001",
                goal="Summarize tickets",
                ticket_files=["ticket-001.txt"],
            ),
            "/api/v1/runs",
            {
                "submission_key": "sdk-ambiguous-001",
                "goal": "Summarize tickets",
                "ticket_files": ["ticket-001.txt"],
                "model_profile_id": "mock-v1",
            },
        ),
        (
            lambda client: client.decide_approval(
                RUN_ID,
                action_hash="a" * 64,
                decision=ApprovalDecision.APPROVE,
            ),
            f"/api/v1/approvals/{RUN_ID}/decision",
            {"action_hash": "a" * 64, "decision": "APPROVE"},
        ),
        (
            lambda client: client.cancel_run(RUN_ID),
            f"/api/v1/runs/{RUN_ID}/cancel",
            None,
        ),
    ],
)
@pytest.mark.parametrize(
    ("exception_factory", "error_type"),
    [
        (
            lambda request: httpx.ReadTimeout("timed out", request=request),
            AgentForgeRequestTimeout,
        ),
        (
            lambda request: httpx.ConnectError("cannot connect", request=request),
            AgentForgeTransportError,
        ),
    ],
)
def test_mutation_transport_failures_map_without_retry(
    operation: Callable[[AgentForgeClient], object],
    expected_path: str,
    expected_body: dict[str, object] | None,
    exception_factory: Callable[[httpx.Request], httpx.RequestError],
    error_type: type[AgentForgeClientError],
) -> None:
    requests: list[httpx.Request] = []

    def fail(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise exception_factory(request)

    with AgentForgeClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(error_type):
            operation(client)

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == expected_path
    if expected_body is not None:
        assert json.loads(requests[0].content) == expected_body


def test_package_exports_the_intentional_sdk_surface() -> None:
    expected = {
        "AgentForgeClient",
        "AgentForgeClientError",
        "AgentForgeConflictError",
        "AgentForgeNotFoundError",
        "AgentForgePollingTimeout",
        "AgentForgeProtocolError",
        "AgentForgeRequestTimeout",
        "AgentForgeRunTerminated",
        "AgentForgeTransportError",
        "AgentForgeUnavailableError",
        "AgentForgeValidationError",
        "Approval",
        "ApprovalDecision",
        "ArtifactContent",
        "Cancellation",
        "ModelProfile",
        "Run",
        "RunDetail",
        "RunListItem",
        "RunState",
        "TERMINAL_RUN_STATES",
        "__version__",
    }

    assert set(agentforge.__all__) == expected
    assert agentforge.__version__ == "0.1.0"


def test_package_import_keeps_http_client_out_of_workflow_imports() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import agentforge; "
                "assert 'httpx' not in sys.modules; "
                "from agentforge import AgentForgeClient; "
                "assert 'httpx' in sys.modules; "
                "assert AgentForgeClient.__name__ == 'AgentForgeClient'"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_wait_for_run_returns_requested_intermediate_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [_run_payload(), _run_payload(state="WAITING_APPROVAL")]
    )
    transport, requests = _recording_transport(
        lambda request: httpx.Response(200, json=next(responses), request=request)
    )
    clock = _FakeClock()
    monkeypatch.setattr(client_module, "time", clock, raising=False)

    with AgentForgeClient(transport=transport) as client:
        run = client.wait_for_run(
            RUN_ID,
            states={RunState.WAITING_APPROVAL},
            timeout_seconds=5,
            poll_interval_seconds=1,
        )

    assert run.state is RunState.WAITING_APPROVAL
    assert [request.method for request in requests] == ["GET", "GET"]
    assert all("/cancel" not in request.url.path for request in requests)


def test_wait_for_run_defaults_to_any_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter([_run_payload(), _run_payload(state="SUCCEEDED")])
    transport, _ = _recording_transport(
        lambda request: httpx.Response(200, json=next(responses), request=request)
    )
    clock = _FakeClock()
    monkeypatch.setattr(client_module, "time", clock, raising=False)

    with AgentForgeClient(transport=transport) as client:
        run = client.wait_for_run(
            RUN_ID, timeout_seconds=5, poll_interval_seconds=1
        )

    assert run.state is RunState.SUCCEEDED


def test_wait_for_run_caps_request_timeout_and_sleep_to_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter([_run_payload(), _run_payload(state="SUCCEEDED")])
    request_timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeout = request.extensions["timeout"]
        assert isinstance(timeout, dict)
        request_timeouts.append(timeout["connect"])
        return httpx.Response(200, json=next(responses), request=request)

    transport, _ = _recording_transport(handler)
    clock = _FakeClock()
    monkeypatch.setattr(client_module, "time", clock, raising=False)

    with AgentForgeClient(timeout_seconds=10, transport=transport) as client:
        client.wait_for_run(
            RUN_ID, timeout_seconds=2.5, poll_interval_seconds=1
        )

    assert request_timeouts == [2.5, 1.5]
    assert clock.sleeps == [1]


def test_wait_for_run_rejects_target_response_completed_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        clock.current += 1.1
        return httpx.Response(
            200, json=_run_payload(state="SUCCEEDED"), request=request
        )

    transport, requests = _recording_transport(handler)
    monkeypatch.setattr(client_module, "time", clock, raising=False)

    with AgentForgeClient(transport=transport) as client:
        with pytest.raises(AgentForgePollingTimeout) as captured:
            client.wait_for_run(
                RUN_ID, timeout_seconds=1, poll_interval_seconds=0.1
            )

    assert captured.value.last_run is not None
    assert captured.value.last_run.state is RunState.SUCCEEDED
    assert len(requests) == 1
    assert clock.sleeps == []


def test_wait_for_run_timeout_retains_last_run_without_cancelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport, requests = _recording_transport(
        lambda request: httpx.Response(200, json=_run_payload(), request=request)
    )
    clock = _FakeClock()
    monkeypatch.setattr(client_module, "time", clock, raising=False)

    with AgentForgeClient(transport=transport) as client:
        with pytest.raises(AgentForgePollingTimeout) as captured:
            client.wait_for_run(
                RUN_ID, timeout_seconds=0.5, poll_interval_seconds=1
            )

    assert captured.value.run_id == UUID(RUN_ID)
    assert captured.value.last_run is not None
    assert captured.value.last_run.state is RunState.RUNNING
    assert [request.method for request in requests] == ["GET"]
    assert all("/cancel" not in request.url.path for request in requests)
    assert clock.sleeps == [0.5]


def test_wait_for_run_raises_on_unrequested_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport, requests = _recording_transport(
        lambda request: httpx.Response(
            200, json=_run_payload(state="FAILED"), request=request
        )
    )
    clock = _FakeClock()
    monkeypatch.setattr(client_module, "time", clock, raising=False)

    with AgentForgeClient(transport=transport) as client:
        with pytest.raises(AgentForgeRunTerminated) as captured:
            client.wait_for_run(
                RUN_ID,
                states={RunState.WAITING_APPROVAL},
                timeout_seconds=5,
            )

    assert captured.value.last_run.state is RunState.FAILED
    assert len(requests) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("failure", ["unavailable", "transport"])
def test_wait_for_run_propagates_request_failure_without_retry(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if failure == "transport":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(
            503, json={"detail": "service unavailable"}, request=request
        )

    clock = _FakeClock()
    monkeypatch.setattr(client_module, "time", clock, raising=False)
    expected = (
        AgentForgeTransportError
        if failure == "transport"
        else AgentForgeUnavailableError
    )

    with AgentForgeClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(expected):
            client.wait_for_run(RUN_ID, timeout_seconds=5)

    assert len(requests) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"states": []},
        {"states": ["NOT_A_STATE"]},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"poll_interval_seconds": 0},
        {"poll_interval_seconds": -1},
        {"poll_interval_seconds": float("nan")},
        {"poll_interval_seconds": float("inf")},
    ],
)
def test_wait_for_run_rejects_empty_states_and_non_positive_timing(
    kwargs: dict[str, object],
) -> None:
    transport, requests = _recording_transport(
        lambda request: httpx.Response(200, json=_run_payload(), request=request)
    )

    with AgentForgeClient(transport=transport) as client:
        with pytest.raises(AgentForgeValidationError):
            client.wait_for_run(RUN_ID, **kwargs)  # type: ignore[arg-type]

    assert requests == []


def _artifact_payload(
    content: str, *, size_bytes: int | None = None, sha256: str | None = None
) -> dict[str, object]:
    content_bytes = content.encode("utf-8")
    return {
        "artifact": {
            "path": f"reports/{RUN_ID}/support-report.md",
            "sha256": sha256 or hashlib.sha256(content_bytes).hexdigest(),
            "size_bytes": len(content_bytes) if size_bytes is None else size_bytes,
            "media_type": "text/markdown",
        },
        "content": content,
    }


def test_get_artifact_accepts_matching_utf8_bytes_and_sha256() -> None:
    content = "# Café\n"
    transport, requests = _recording_transport(
        lambda request: httpx.Response(
            200, json=_artifact_payload(content), request=request
        )
    )

    with AgentForgeClient(transport=transport) as client:
        artifact = client.get_artifact(RUN_ID)

    assert artifact.content == content
    assert artifact.artifact.size_bytes == len(content.encode("utf-8"))
    assert requests[0].url.path == f"/api/v1/runs/{RUN_ID}/artifact"


def test_get_artifact_rejects_wrong_unicode_byte_length() -> None:
    content = "# Café\n"
    transport, _ = _recording_transport(
        lambda request: httpx.Response(
            200,
            json=_artifact_payload(content, size_bytes=len(content)),
            request=request,
        )
    )

    with AgentForgeClient(transport=transport) as client:
        with pytest.raises(AgentForgeProtocolError) as captured:
            client.get_artifact(RUN_ID)

    assert content not in str(captured.value)
    assert content not in repr(captured.value)


def test_get_artifact_rejects_wrong_sha256_without_exposing_content() -> None:
    content = "# Café\n"
    transport, _ = _recording_transport(
        lambda request: httpx.Response(
            200,
            json=_artifact_payload(content, sha256="0" * 64),
            request=request,
        )
    )

    with AgentForgeClient(transport=transport) as client:
        with pytest.raises(AgentForgeProtocolError) as captured:
            client.get_artifact(RUN_ID)

    assert content not in str(captured.value)
    assert content not in repr(captured.value)
