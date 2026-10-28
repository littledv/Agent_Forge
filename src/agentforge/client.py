"""Synchronous typed client for the AgentForge v1 HTTP API."""

from __future__ import annotations

from collections.abc import Collection, Sequence
import hashlib
import math
import time
from typing import TypeVar, cast
from uuid import UUID

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

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
    ArtifactContent,
    Cancellation,
    ModelProfile,
    Run,
    RunDetail,
    RunListItem,
    TERMINAL_RUN_STATES,
)
from agentforge.domain import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    RunState,
    SubmitRun,
)


_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)

_STATUS_ERRORS: dict[int, type[AgentForgeClientError]] = {
    404: AgentForgeNotFoundError,
    409: AgentForgeConflictError,
    422: AgentForgeValidationError,
    503: AgentForgeUnavailableError,
}


class AgentForgeClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        trust_env: bool = False,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise AgentForgeValidationError(
                "create_client",
                detail="timeout_seconds must be finite and positive",
            )
        self._timeout_seconds = timeout_seconds
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            trust_env=trust_env,
            follow_redirects=False,
        )
        self._closed = False

    def __enter__(self) -> AgentForgeClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._client.close()
        self._closed = True

    def list_model_profiles(self) -> tuple[ModelProfile, ...]:
        return cast(
            tuple[ModelProfile, ...],
            self._request(
                operation="list_model_profiles",
                method="GET",
                path="/api/v1/model-profiles",
                expected_statuses={200},
                response_model=ModelProfile,
                many=True,
            ),
        )

    def submit_run(
        self,
        *,
        submission_key: str,
        goal: str,
        ticket_files: Sequence[str],
        model_profile_id: str = "mock-v1",
    ) -> Run:
        if isinstance(ticket_files, (str, bytes)):
            raise AgentForgeValidationError(
                "submit_run", detail="invalid submission"
            )
        try:
            request = SubmitRun(
                submission_key=submission_key,
                goal=goal,
                ticket_files=tuple(ticket_files),
                model_profile_id=model_profile_id,
            )
        except (TypeError, ValidationError):
            raise AgentForgeValidationError(
                "submit_run", detail="invalid submission"
            ) from None
        return cast(
            Run,
            self._request(
                operation="submit_run",
                method="POST",
                path="/api/v1/runs",
                expected_statuses={200, 202},
                response_model=Run,
                json_body=request.model_dump(mode="json"),
            ),
        )

    def list_runs(self, *, limit: int = 20) -> tuple[RunListItem, ...]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise AgentForgeValidationError(
                "list_runs", detail="limit must be between 1 and 50"
            )
        return cast(
            tuple[RunListItem, ...],
            self._request(
                operation="list_runs",
                method="GET",
                path="/api/v1/runs",
                expected_statuses={200},
                response_model=RunListItem,
                query={"limit": limit},
                many=True,
            ),
        )

    def get_run(self, run_id: UUID | str) -> Run:
        normalized = self._normalize_uuid(run_id, operation="get_run")
        return self._get_run_with_timeout(normalized, self._timeout_seconds)

    def _get_run_with_timeout(self, run_id: UUID, timeout: float) -> Run:
        return cast(
            Run,
            self._request(
                operation="get_run",
                method="GET",
                path=f"/api/v1/runs/{run_id}",
                expected_statuses={200},
                response_model=Run,
                timeout=timeout,
            ),
        )

    def get_run_detail(self, run_id: UUID | str) -> RunDetail:
        normalized = self._normalize_uuid(run_id, operation="get_run_detail")
        return cast(
            RunDetail,
            self._request(
                operation="get_run_detail",
                method="GET",
                path=f"/api/v1/runs/{normalized}/detail",
                expected_statuses={200},
                response_model=RunDetail,
            ),
        )

    def get_approval(self, run_id: UUID | str) -> Approval:
        normalized = self._normalize_uuid(run_id, operation="get_approval")
        return cast(
            Approval,
            self._request(
                operation="get_approval",
                method="GET",
                path=f"/api/v1/runs/{normalized}/approval",
                expected_statuses={200},
                response_model=Approval,
            ),
        )

    def decide_approval(
        self,
        approval_id: UUID | str,
        *,
        action_hash: str,
        decision: ApprovalDecision | str,
    ) -> Approval:
        normalized = self._normalize_uuid(
            approval_id, operation="decide_approval"
        )
        try:
            request = ApprovalDecisionRequest(
                action_hash=action_hash,
                decision=decision,
            )
        except (TypeError, ValidationError):
            raise AgentForgeValidationError(
                "decide_approval", detail="invalid approval decision"
            ) from None
        return cast(
            Approval,
            self._request(
                operation="decide_approval",
                method="POST",
                path=f"/api/v1/approvals/{normalized}/decision",
                expected_statuses={200, 202},
                response_model=Approval,
                json_body=request.model_dump(mode="json"),
            ),
        )

    def cancel_run(self, run_id: UUID | str) -> Cancellation:
        normalized = self._normalize_uuid(run_id, operation="cancel_run")
        return cast(
            Cancellation,
            self._request(
                operation="cancel_run",
                method="POST",
                path=f"/api/v1/runs/{normalized}/cancel",
                expected_statuses={200, 202},
                response_model=Cancellation,
            ),
        )

    def wait_for_run(
        self,
        run_id: UUID | str,
        *,
        states: Collection[RunState | str] = TERMINAL_RUN_STATES,
        timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 1.0,
    ) -> Run:
        normalized_id = self._normalize_uuid(run_id, operation="wait_for_run")
        target_states = self._normalize_states(states)
        self._validate_positive_finite(
            timeout_seconds, operation="wait_for_run", name="timeout_seconds"
        )
        self._validate_positive_finite(
            poll_interval_seconds,
            operation="wait_for_run",
            name="poll_interval_seconds",
        )

        deadline = time.monotonic() + timeout_seconds
        last_run: Run | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgentForgePollingTimeout(
                    "wait_for_run", run_id=normalized_id, last_run=last_run
                )
            last_run = self._get_run_with_timeout(
                normalized_id, min(self._timeout_seconds, remaining)
            )
            if deadline - time.monotonic() <= 0:
                raise AgentForgePollingTimeout(
                    "wait_for_run", run_id=normalized_id, last_run=last_run
                )
            if last_run.state in target_states:
                return last_run
            if last_run.state in TERMINAL_RUN_STATES:
                raise AgentForgeRunTerminated(
                    "wait_for_run", run_id=normalized_id, last_run=last_run
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgentForgePollingTimeout(
                    "wait_for_run", run_id=normalized_id, last_run=last_run
                )
            time.sleep(min(poll_interval_seconds, remaining))

    def get_artifact(self, run_id: UUID | str) -> ArtifactContent:
        normalized = self._normalize_uuid(run_id, operation="get_artifact")
        artifact = cast(
            ArtifactContent,
            self._request(
                operation="get_artifact",
                method="GET",
                path=f"/api/v1/runs/{normalized}/artifact",
                expected_statuses={200},
                response_model=ArtifactContent,
            ),
        )
        content_bytes = artifact.content.encode("utf-8")
        if (
            len(content_bytes) != artifact.artifact.size_bytes
            or hashlib.sha256(content_bytes).hexdigest()
            != artifact.artifact.sha256
        ):
            raise AgentForgeProtocolError(
                "get_artifact",
                status_code=200,
                detail="artifact integrity check failed",
            )
        return artifact

    @staticmethod
    def _normalize_uuid(value: UUID | str, *, operation: str) -> UUID:
        try:
            if isinstance(value, UUID):
                return value
            if not isinstance(value, str):
                raise TypeError
            return UUID(value)
        except (TypeError, ValueError, AttributeError):
            raise AgentForgeValidationError(
                operation, detail="invalid UUID"
            ) from None

    @staticmethod
    def _normalize_states(
        states: Collection[RunState | str],
    ) -> frozenset[RunState]:
        if isinstance(states, (str, bytes)):
            raise AgentForgeValidationError(
                "wait_for_run", detail="states must be a non-empty collection"
            )
        try:
            normalized = frozenset(
                state if isinstance(state, RunState) else RunState(state)
                for state in states
            )
        except (TypeError, ValueError):
            raise AgentForgeValidationError(
                "wait_for_run", detail="states contains an invalid run state"
            ) from None
        if not normalized:
            raise AgentForgeValidationError(
                "wait_for_run", detail="states must be a non-empty collection"
            )
        return normalized

    @staticmethod
    def _validate_positive_finite(
        value: float, *, operation: str, name: str
    ) -> None:
        try:
            valid = math.isfinite(value) and value > 0
        except TypeError:
            valid = False
        if not valid:
            raise AgentForgeValidationError(
                operation, detail=f"{name} must be finite and positive"
            )

    def _request(
        self,
        *,
        operation: str,
        method: str,
        path: str,
        expected_statuses: set[int],
        response_model: type[_ResponseModel],
        json_body: dict[str, object] | None = None,
        query: dict[str, object] | None = None,
        many: bool = False,
        timeout: float | None = None,
    ) -> _ResponseModel | tuple[_ResponseModel, ...]:
        try:
            response = self._client.request(
                method,
                path,
                json=json_body,
                params=query,
                timeout=self._timeout_seconds if timeout is None else timeout,
            )
        except httpx.TimeoutException:
            raise AgentForgeRequestTimeout(
                operation, detail="request timed out"
            ) from None
        except httpx.RequestError:
            raise AgentForgeTransportError(
                operation, detail="transport failed"
            ) from None

        if response.status_code not in expected_statuses:
            error_type = _STATUS_ERRORS.get(response.status_code)
            if error_type is None:
                raise AgentForgeProtocolError(
                    operation,
                    status_code=response.status_code,
                    detail="unexpected HTTP status",
                )
            raise error_type(
                operation,
                status_code=response.status_code,
                detail=self._safe_error_detail(response),
            )

        try:
            if many:
                adapter = TypeAdapter(tuple[response_model, ...])
                return adapter.validate_json(response.content, strict=True)
            return response_model.model_validate_json(response.content)
        except (TypeError, ValueError, ValidationError):
            raise AgentForgeProtocolError(
                operation,
                status_code=response.status_code,
                detail="invalid response",
            ) from None

    @staticmethod
    def _safe_error_detail(response: httpx.Response) -> str | None:
        try:
            body = response.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
        if response.status_code == 422 and isinstance(detail, list):
            return "request validation failed"
        return None


__all__ = ["AgentForgeClient"]
