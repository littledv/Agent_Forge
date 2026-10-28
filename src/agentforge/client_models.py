"""Typed, additive-compatible response models for the public Python client."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
)

from agentforge.domain import (
    ApprovalState,
    AttemptState,
    DeliveryContent,
    DisplayName,
    FailureKind,
    Goal,
    MediaType,
    ModelName,
    ModelProfileId,
    ModelProvider,
    RunState,
    Sha256,
    StepState,
    TaskQueue,
)


class _ClientModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


def _validate_relative_path(value: str) -> str:
    if not value or "\\" in value:
        raise ValueError("path must be a non-empty POSIX-style relative path")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise ValueError("path must be relative")
    if any(part in {"", ".", ".."} for part in PurePosixPath(value).parts):
        raise ValueError("path must not contain empty, current, or parent components")
    return value


class _ArtifactRef(_ClientModel):
    path: str
    sha256: Sha256
    size_bytes: NonNegativeInt
    media_type: MediaType

    _path_is_relative = field_validator("path")(_validate_relative_path)


class ModelProfile(_ClientModel):
    profile_id: ModelProfileId
    display_name: DisplayName
    provider: ModelProvider
    model_name: ModelName
    model_digest: Sha256 | None


class Run(_ClientModel):
    run_id: UUID
    workflow_id: str
    state: RunState
    created_at: datetime
    updated_at: datetime
    failure_kind: FailureKind | None = None
    artifact: _ArtifactRef | None = None


class RunListItem(_ClientModel):
    run_id: UUID
    goal: Goal
    state: RunState
    failure_kind: FailureKind | None
    created_at: datetime
    updated_at: datetime


class _ModelProfileSnapshot(_ClientModel):
    profile_id: ModelProfileId
    provider: ModelProvider
    model_name: ModelName
    model_digest: Sha256 | None
    task_queue: TaskQueue
    context_tokens: NonNegativeInt
    max_output_tokens: NonNegativeInt
    max_prompt_chars: NonNegativeInt
    max_report_chars: NonNegativeInt
    max_repairs: NonNegativeInt


class _Attempt(_ClientModel):
    attempt_number: PositiveInt
    state: AttemptState
    failure_kind: FailureKind | None
    created_at: datetime
    updated_at: datetime
    model_provider: ModelProvider | None = None
    model_name: ModelName | None = None
    model_digest: Sha256 | None = None
    model_request_count: NonNegativeInt | None = None
    model_input_tokens: NonNegativeInt | None = None
    model_output_tokens: NonNegativeInt | None = None
    model_duration_ms: NonNegativeInt | None = None
    provider_cost_estimate_usd: Annotated[float, Field(ge=0)] | None = None
    input_tokens_estimated: bool | None = None
    usage_uncertain: bool | None = None


class _Step(_ClientModel):
    ordinal: NonNegativeInt
    name: str
    state: StepState
    created_at: datetime
    updated_at: datetime
    attempts: tuple[_Attempt, ...]


class _DeliverReportAction(_ClientModel):
    type: Literal["deliver_report"]
    target: Literal["simulated-inbox"]
    artifact: _ArtifactRef


class Approval(_ClientModel):
    approval_id: UUID
    run_id: UUID
    operation_id: UUID
    action_hash: Sha256
    action: _DeliverReportAction
    state: ApprovalState
    expires_at: datetime
    created_at: datetime
    decided_at: datetime | None = None


class _DeliveryReceipt(_ClientModel):
    delivery_id: UUID
    operation_id: UUID
    artifact_sha256: Sha256
    committed_at: datetime


class RunDetail(_ClientModel):
    run: Run
    goal: Goal
    ticket_files: tuple[str, ...]
    model_profile: _ModelProfileSnapshot
    projection_sequence: NonNegativeInt
    steps: tuple[_Step, ...]
    approval: Approval | None
    cancellation_requested: bool
    delivery: _DeliveryReceipt | None


class Cancellation(_ClientModel):
    request_id: UUID
    run_id: UUID
    created_at: datetime


class ArtifactContent(_ClientModel):
    artifact: _ArtifactRef
    content: DeliveryContent


TERMINAL_RUN_STATES: frozenset[RunState] = frozenset(
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


__all__ = [
    "Approval",
    "ArtifactContent",
    "Cancellation",
    "ModelProfile",
    "Run",
    "RunDetail",
    "RunListItem",
    "TERMINAL_RUN_STATES",
]
