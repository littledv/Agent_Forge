"""Version-stable data contracts shared across AgentForge components."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)


class RunState(str, Enum):
    PENDING_START = "PENDING_START"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


class StepState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    FAILED = "FAILED"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"
    UNCERTAIN = "UNCERTAIN"


class AttemptState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FailureKind(str, Enum):
    RETRYABLE = "RETRYABLE"
    PERMANENT = "PERMANENT"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"
    UNCERTAIN = "UNCERTAIN"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ApiErrorResponse(StrictModel):
    detail: str | list[dict[str, object]]


SubmissionKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
Goal = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_096)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MediaType = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
DeliveryContent = Annotated[str, StringConstraints(min_length=1, max_length=262_144)]
ExternalUUID = Annotated[UUID, Field(strict=False)]
ModelProfileId = Literal["mock-v1", "ollama-qwen3-4b-instruct-q4-v1"]
ModelProvider = Literal["mock", "ollama"]
ModelName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
TaskQueue = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
DisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]


class ModelProfileSnapshot(StrictModel):
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

    @model_validator(mode="after")
    def validate_identity(self) -> ModelProfileSnapshot:
        expected_provider = (
            "mock"
            if self.profile_id == "mock-v1"
            else "ollama"
        )
        if self.provider != expected_provider:
            raise ValueError("model profile provider does not match its ID")
        if self.provider == "mock" and self.model_digest is not None:
            raise ValueError("mock model profile must not have a digest")
        if self.provider == "ollama" and self.model_digest is None:
            raise ValueError("Ollama model profile requires a digest")
        return self


class ModelProfileDescriptor(StrictModel):
    profile_id: ModelProfileId
    display_name: DisplayName
    provider: ModelProvider
    model_name: ModelName
    model_digest: Sha256 | None


class ModelIdentity(StrictModel):
    provider: ModelProvider
    model_name: ModelName
    model_digest: Sha256 | None

    @model_validator(mode="after")
    def validate_digest(self) -> ModelIdentity:
        if self.provider == "mock" and self.model_digest is not None:
            raise ValueError("mock model identity must not have a digest")
        if self.provider == "ollama" and self.model_digest is None:
            raise ValueError("Ollama model identity requires a digest")
        return self


class ModelResponseUsage(StrictModel):
    input_tokens: NonNegativeInt | None
    output_tokens: NonNegativeInt | None
    duration_ms: NonNegativeInt | None


def default_mock_model_profile() -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id="mock-v1",
        provider="mock",
        model_name="deterministic-support-report-v1",
        model_digest=None,
        task_queue="agentforge-support-report-v1",
        context_tokens=0,
        max_output_tokens=0,
        max_prompt_chars=0,
        max_report_chars=0,
        max_repairs=0,
    )


def _validate_relative_path(value: str) -> str:
    if not value or "\\" in value:
        raise ValueError("path must be a non-empty POSIX-style relative path")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise ValueError("path must be relative")
    if any(part in {"", ".", ".."} for part in PurePosixPath(value).parts):
        raise ValueError("path must not contain empty, current, or parent components")
    return value


def _validate_ticket_files(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        value = tuple(value)
    if not isinstance(value, tuple) or not value:
        raise ValueError("ticket_files must contain at least one relative path")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("ticket_files entries must be strings")
    return tuple(_validate_relative_path(item) for item in value)


class FileReference(StrictModel):
    path: str
    sha256: Sha256
    size_bytes: NonNegativeInt

    _path_is_relative = field_validator("path")(_validate_relative_path)


class TicketSnapshotRef(FileReference):
    ticket_count: NonNegativeInt


class DraftRef(FileReference):
    pass


class ArtifactRef(FileReference):
    media_type: MediaType


class ApprovalState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


ExternalApprovalDecision = Annotated[ApprovalDecision, Field(strict=False)]


class DeliverReportAction(StrictModel):
    type: Literal["deliver_report"]
    target: Literal["simulated-inbox"]
    artifact: ArtifactRef


class DeliveryAuthorization(StrictModel):
    approval_id: UUID
    operation_id: UUID
    action_hash: Sha256
    action: DeliverReportAction


class ApprovalSnapshot(StrictModel):
    approval_id: UUID
    run_id: UUID
    operation_id: UUID
    action_hash: Sha256
    action: DeliverReportAction
    state: ApprovalState
    expires_at: datetime
    created_at: datetime
    decided_at: datetime | None = None


class ApprovalDecisionRequest(StrictModel):
    action_hash: Sha256
    decision: ExternalApprovalDecision


class ApprovalSignal(StrictModel):
    message_id: UUID
    approval_id: UUID
    action_hash: Sha256
    decision: ApprovalDecision


class CancellationSignal(StrictModel):
    message_id: UUID
    request_id: UUID
    run_id: UUID


class CancellationSnapshot(StrictModel):
    request_id: UUID
    run_id: UUID
    created_at: datetime


class DeliveryRequest(StrictModel):
    operation_id: ExternalUUID
    action_hash: Sha256
    artifact: ArtifactRef
    content: DeliveryContent


class DeliveryReceipt(StrictModel):
    delivery_id: UUID
    operation_id: UUID
    artifact_sha256: Sha256
    committed_at: datetime


class ArtifactContent(StrictModel):
    artifact: ArtifactRef
    content: DeliveryContent


class SubmitRun(StrictModel):
    submission_key: SubmissionKey
    goal: Goal
    ticket_files: tuple[str, ...]
    model_profile_id: ModelProfileId = "mock-v1"

    _ticket_files_are_relative = field_validator("ticket_files", mode="before")(
        _validate_ticket_files
    )


class RunSnapshot(StrictModel):
    run_id: UUID
    workflow_id: str
    state: RunState
    created_at: datetime
    updated_at: datetime
    failure_kind: FailureKind | None = None
    artifact: ArtifactRef | None = None


class RunListItem(StrictModel):
    run_id: UUID
    goal: Goal
    state: RunState
    failure_kind: FailureKind | None
    created_at: datetime
    updated_at: datetime


class AttemptSnapshot(StrictModel):
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


class StepSnapshot(StrictModel):
    ordinal: NonNegativeInt
    name: str
    state: StepState
    created_at: datetime
    updated_at: datetime
    attempts: tuple[AttemptSnapshot, ...]


class RunDetailSnapshot(StrictModel):
    run: RunSnapshot
    goal: Goal
    ticket_files: tuple[str, ...]
    model_profile: ModelProfileSnapshot
    projection_sequence: NonNegativeInt
    steps: tuple[StepSnapshot, ...]
    approval: ApprovalSnapshot | None
    cancellation_requested: bool
    delivery: DeliveryReceipt | None


class WorkflowInput(StrictModel):
    run_id: UUID
    goal: Goal
    ticket_files: tuple[str, ...]
    max_decisions: PositiveInt
    model_profile: ModelProfileSnapshot = Field(
        default_factory=default_mock_model_profile
    )

    _ticket_files_are_relative = field_validator("ticket_files", mode="before")(
        _validate_ticket_files
    )


class ModelDecisionInput(StrictModel):
    run_id: UUID
    goal: Goal
    ticket_files: tuple[str, ...]
    decision_number: PositiveInt
    ticket_snapshot: TicketSnapshotRef | None = None
    draft: DraftRef | None = None
    artifact: ArtifactRef | None = None
    delivery: DeliveryReceipt | None = None
    model_profile: ModelProfileSnapshot = Field(
        default_factory=default_mock_model_profile
    )

    _ticket_files_are_relative = field_validator("ticket_files", mode="before")(
        _validate_ticket_files
    )


class ReadTicketsAction(StrictModel):
    type: Literal["read_tickets"]
    ticket_files: tuple[str, ...]

    _ticket_files_are_relative = field_validator("ticket_files", mode="before")(
        _validate_ticket_files
    )


class PublishReportAction(StrictModel):
    type: Literal["publish_report"]
    ticket_snapshot: TicketSnapshotRef
    draft: DraftRef


class FinishAction(StrictModel):
    type: Literal["finish"]
    artifact: ArtifactRef


AgentAction = Annotated[
    ReadTicketsAction | PublishReportAction | DeliverReportAction | FinishAction,
    Field(discriminator="type"),
]


class ProjectionEvent(StrictModel):
    event_id: UUID
    run_id: UUID
    sequence: NonNegativeInt
    run_state: RunState
    step_id: UUID | None = None
    step_ordinal: NonNegativeInt | None = None
    step_name: str | None = None
    step_state: StepState | None = None
    attempt_id: UUID | None = None
    attempt_number: PositiveInt | None = None
    attempt_state: AttemptState | None = None
    failure_kind: FailureKind | None = None
    artifact: ArtifactRef | None = None
