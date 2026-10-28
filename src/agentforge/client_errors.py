"""Sanitized public exceptions raised by the AgentForge Python client."""

from __future__ import annotations

from uuid import UUID

from agentforge.client_models import Run, TERMINAL_RUN_STATES


class AgentForgeClientError(Exception):
    def __init__(
        self,
        operation: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.operation = operation
        self.status_code = status_code
        self.detail = detail
        parts = [f"{operation} failed"]
        if status_code is not None:
            parts.append(f"HTTP {status_code}")
        if detail is not None:
            parts.append(detail)
        super().__init__(": ".join(parts))


class AgentForgeValidationError(AgentForgeClientError):
    pass


class AgentForgeNotFoundError(AgentForgeClientError):
    pass


class AgentForgeConflictError(AgentForgeClientError):
    pass


class AgentForgeUnavailableError(AgentForgeClientError):
    pass


class AgentForgeTransportError(AgentForgeClientError):
    pass


class AgentForgeRequestTimeout(AgentForgeTransportError):
    pass


class AgentForgeProtocolError(AgentForgeClientError):
    pass


class AgentForgePollingTimeout(AgentForgeClientError):
    def __init__(
        self,
        operation: str,
        *,
        run_id: UUID,
        last_run: Run | None,
    ) -> None:
        self.run_id = run_id
        self.last_run = last_run
        super().__init__(operation, detail="polling deadline expired")


class AgentForgeRunTerminated(AgentForgeClientError):
    def __init__(
        self,
        operation: str,
        *,
        run_id: UUID,
        last_run: Run,
    ) -> None:
        if last_run.state not in TERMINAL_RUN_STATES:
            raise ValueError("last_run must be terminal")
        self.run_id = run_id
        self.last_run = last_run
        super().__init__(
            operation,
            detail=f"run reached unrequested terminal state {last_run.state.value}",
        )


__all__ = [
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
]
