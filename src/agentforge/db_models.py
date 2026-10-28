"""SQLAlchemy mappings for PostgreSQL-owned AgentForge records."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    BigInteger,
    Boolean,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agentforge.model_profiles import MOCK_MODEL_PROFILE


RUN_STATES = (
    "PENDING_START",
    "RUNNING",
    "WAITING_APPROVAL",
    "CANCELLATION_REQUESTED",
    "SUCCEEDED",
    "FAILED",
    "DENIED",
    "CANCELLED",
    "TIMED_OUT",
    "BUDGET_EXHAUSTED",
    "NEEDS_ATTENTION",
)
STEP_STATES = (
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "WAITING_APPROVAL",
    "RETRY_SCHEDULED",
    "FAILED",
    "DENIED",
    "CANCELLED",
    "UNCERTAIN",
)
ATTEMPT_STATES = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")
FAILURE_KINDS = ("RETRYABLE", "PERMANENT", "DENIED", "CANCELLED", "UNCERTAIN")
APPROVAL_STATES = ("PENDING", "APPROVED", "REJECTED", "EXPIRED")
MODEL_PROVIDERS = ("mock", "ollama")
MOCK_MODEL_PROFILE_JSON = json.dumps(
    MOCK_MODEL_PROFILE.model_dump(mode="json"),
    sort_keys=True,
    separators=(",", ":"),
)
MOCK_MODEL_PROFILE_SQL_JSON = MOCK_MODEL_PROFILE_JSON.replace(":", r"\:")


def _in_constraint(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


class Base(DeclarativeBase):
    pass


class AgentDefinitionRecord(Base):
    __tablename__ = "agent_definitions"
    __table_args__ = (UniqueConstraint("slug", name="uq_agent_definitions_slug"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentVersionRecord(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint(
            "agent_definition_id", "version", name="uq_agent_versions_definition_version"
        ),
        CheckConstraint("version > 0", name="ck_agent_versions_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    agent_definition_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_definitions.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("submission_key", name="uq_runs_submission_key"),
        UniqueConstraint("workflow_id", name="uq_runs_workflow_id"),
        CheckConstraint(_in_constraint("state", RUN_STATES), name="ck_runs_state"),
        CheckConstraint(
            "failure_kind IS NULL OR " + _in_constraint("failure_kind", FAILURE_KINDS),
            name="ck_runs_failure_kind",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name="ck_runs_request_hash_sha256"
        ),
        CheckConstraint(
            "projection_sequence >= 0", name="ck_runs_projection_sequence_nonnegative"
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    agent_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_versions.id"), nullable=False
    )
    submission_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(f"'{MOCK_MODEL_PROFILE_SQL_JSON}'::jsonb"),
    )
    workflow_id: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    projection_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    failure_kind: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StepRecord(Base):
    __tablename__ = "steps"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_steps_run_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_steps_ordinal_nonnegative"),
        CheckConstraint(_in_constraint("state", STEP_STATES), name="ck_steps_state"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AttemptRecord(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("step_id", "attempt_number", name="uq_attempts_step_number"),
        CheckConstraint("attempt_number > 0", name="ck_attempts_number_positive"),
        CheckConstraint(
            _in_constraint("state", ATTEMPT_STATES), name="ck_attempts_state"
        ),
        CheckConstraint(
            "failure_kind IS NULL OR " + _in_constraint("failure_kind", FAILURE_KINDS),
            name="ck_attempts_failure_kind",
        ),
        CheckConstraint(
            "model_provider IS NULL OR "
            + _in_constraint("model_provider", MODEL_PROVIDERS),
            name="ck_attempts_model_provider",
        ),
        CheckConstraint(
            "model_digest IS NULL OR model_digest ~ '^[0-9a-f]{64}$'",
            name="ck_attempts_model_digest_sha256",
        ),
        CheckConstraint(
            "model_request_count IS NULL OR model_request_count >= 0",
            name="ck_attempts_model_request_count_nonnegative",
        ),
        CheckConstraint(
            "model_exact_input_count IS NULL OR model_exact_input_count >= 0",
            name="ck_attempts_model_exact_input_count_nonnegative",
        ),
        CheckConstraint(
            "model_exact_input_count IS NULL OR "
            "(model_request_count IS NOT NULL AND "
            "model_exact_input_count <= model_request_count)",
            name="ck_attempts_model_exact_input_count_bounded",
        ),
        CheckConstraint(
            "model_input_tokens IS NULL OR model_input_tokens >= 0",
            name="ck_attempts_model_input_tokens_nonnegative",
        ),
        CheckConstraint(
            "model_output_tokens IS NULL OR model_output_tokens >= 0",
            name="ck_attempts_model_output_tokens_nonnegative",
        ),
        CheckConstraint(
            "model_duration_ms IS NULL OR model_duration_ms >= 0",
            name="ck_attempts_model_duration_ms_nonnegative",
        ),
        CheckConstraint(
            "provider_cost_estimate_usd IS NULL OR provider_cost_estimate_usd >= 0",
            name="ck_attempts_provider_cost_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    step_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("steps.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_kind: Mapped[str | None] = mapped_column(String(32))
    model_provider: Mapped[str | None] = mapped_column(String(32))
    model_name: Mapped[str | None] = mapped_column(String(256))
    model_digest: Mapped[str | None] = mapped_column(String(64))
    model_request_count: Mapped[int | None] = mapped_column(Integer)
    model_exact_input_count: Mapped[int | None] = mapped_column(Integer)
    model_input_tokens: Mapped[int | None] = mapped_column(Integer)
    model_output_tokens: Mapped[int | None] = mapped_column(Integer)
    model_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    provider_cost_estimate_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6)
    )
    input_tokens_estimated: Mapped[bool | None] = mapped_column(Boolean)
    usage_uncertain: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "path", name="uq_artifacts_run_path"),
        CheckConstraint("size_bytes >= 0", name="ck_artifacts_size_nonnegative"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_artifacts_sha256"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkflowStartOutboxRecord(Base):
    __tablename__ = "workflow_start_outbox"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_workflow_start_outbox_run"),
        CheckConstraint("attempt_count >= 0", name="ck_outbox_attempt_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(String(256), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProjectionEventRecord(Base):
    __tablename__ = "projection_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_projection_events_event_id"),
        CheckConstraint("sequence >= 0", name="ck_projection_events_sequence_nonnegative"),
    )

    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApprovalRecord(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("run_id", "action_hash", name="uq_approvals_run_action_hash"),
        UniqueConstraint("operation_id", name="uq_approvals_operation_id"),
        CheckConstraint(_in_constraint("state", APPROVAL_STATES), name="ck_approvals_state"),
        CheckConstraint(
            "action_hash ~ '^[0-9a-f]{64}$'",
            name="ck_approvals_action_hash_sha256",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CancellationRequestRecord(Base):
    __tablename__ = "cancellation_requests"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_cancellation_requests_run"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DeliveryRecord(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("receiver_delivery_id", name="uq_deliveries_receiver_id"),
        CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_deliveries_artifact_sha256",
        ),
    )

    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    approval_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("approvals.id", ondelete="CASCADE"),
        nullable=False,
    )
    receiver_delivery_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkflowSignalOutboxRecord(Base):
    __tablename__ = "workflow_signal_outbox"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "signal_name",
            "message_id",
            name="uq_workflow_signal_outbox_message",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_workflow_signal_outbox_attempt_nonnegative",
        ),
    )

    message_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(String(256), nullable=False)
    signal_name: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
