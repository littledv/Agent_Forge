"""AgentForge durable local agent runtime."""

from __future__ import annotations

from typing import Any


__version__ = "0.1.0"

_LAZY_EXPORTS = {
    "AgentForgeClient": ("agentforge.client", "AgentForgeClient"),
    "AgentForgeClientError": ("agentforge.client_errors", "AgentForgeClientError"),
    "AgentForgeConflictError": (
        "agentforge.client_errors",
        "AgentForgeConflictError",
    ),
    "AgentForgeNotFoundError": (
        "agentforge.client_errors",
        "AgentForgeNotFoundError",
    ),
    "AgentForgePollingTimeout": (
        "agentforge.client_errors",
        "AgentForgePollingTimeout",
    ),
    "AgentForgeProtocolError": (
        "agentforge.client_errors",
        "AgentForgeProtocolError",
    ),
    "AgentForgeRequestTimeout": (
        "agentforge.client_errors",
        "AgentForgeRequestTimeout",
    ),
    "AgentForgeRunTerminated": (
        "agentforge.client_errors",
        "AgentForgeRunTerminated",
    ),
    "AgentForgeTransportError": (
        "agentforge.client_errors",
        "AgentForgeTransportError",
    ),
    "AgentForgeUnavailableError": (
        "agentforge.client_errors",
        "AgentForgeUnavailableError",
    ),
    "AgentForgeValidationError": (
        "agentforge.client_errors",
        "AgentForgeValidationError",
    ),
    "Approval": ("agentforge.client_models", "Approval"),
    "ApprovalDecision": ("agentforge.domain", "ApprovalDecision"),
    "ArtifactContent": ("agentforge.client_models", "ArtifactContent"),
    "Cancellation": ("agentforge.client_models", "Cancellation"),
    "ModelProfile": ("agentforge.client_models", "ModelProfile"),
    "Run": ("agentforge.client_models", "Run"),
    "RunDetail": ("agentforge.client_models", "RunDetail"),
    "RunListItem": ("agentforge.client_models", "RunListItem"),
    "RunState": ("agentforge.domain", "RunState"),
    "TERMINAL_RUN_STATES": (
        "agentforge.client_models",
        "TERMINAL_RUN_STATES",
    ),
}

__all__ = [*_LAZY_EXPORTS, "__version__"]


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
