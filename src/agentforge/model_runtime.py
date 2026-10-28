"""Dependency-neutral contracts for model decision providers."""

from __future__ import annotations

from typing import Protocol

from agentforge.domain import AgentAction, ModelDecisionInput


class ModelUsageReporter(Protocol):
    async def request_started(self, *, estimated_input_tokens: int) -> None: ...

    async def response_completed(
        self,
        *,
        estimated_input_tokens: int,
        input_tokens: int | None,
        output_tokens: int | None,
        duration_ms: int | None,
    ) -> None: ...


class NoOpModelUsageReporter:
    async def request_started(self, *, estimated_input_tokens: int) -> None:
        return None

    async def response_completed(
        self,
        *,
        estimated_input_tokens: int,
        input_tokens: int | None,
        output_tokens: int | None,
        duration_ms: int | None,
    ) -> None:
        return None


class DecisionProvider(Protocol):
    async def decide(
        self, input: ModelDecisionInput, usage: ModelUsageReporter
    ) -> AgentAction: ...
