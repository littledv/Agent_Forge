"""Create AgentForge application records.

Revision ID: 0001_initial_records
Revises: None
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from agentforge.db_models import ATTEMPT_STATES, FAILURE_KINDS, RUN_STATES, STEP_STATES


revision = "0001_initial_records"
down_revision = None
branch_labels = None
depends_on = None

DEFINITION_ID = UUID("00000000-0000-0000-0000-000000000101")
VERSION_ID = UUID("00000000-0000-0000-0000-000000000102")


def _in_constraint(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    op.create_table(
        "agent_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_agent_definitions_slug"),
    )
    op.create_table(
        "agent_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_definition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_definitions.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_agent_versions_version_positive"),
        sa.UniqueConstraint("agent_definition_id", "version", name="uq_agent_versions_definition_version"),
    )
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_versions.id"), nullable=False),
        sa.Column("submission_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column("workflow_id", sa.String(256), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("projection_sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_kind", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in_constraint("state", RUN_STATES), name="ck_runs_state"),
        sa.CheckConstraint("failure_kind IS NULL OR " + _in_constraint("failure_kind", FAILURE_KINDS), name="ck_runs_failure_kind"),
        sa.CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_runs_request_hash_sha256"),
        sa.CheckConstraint("projection_sequence >= 0", name="ck_runs_projection_sequence_nonnegative"),
        sa.UniqueConstraint("submission_key", name="uq_runs_submission_key"),
        sa.UniqueConstraint("workflow_id", name="uq_runs_workflow_id"),
    )
    op.create_table(
        "steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_steps_ordinal_nonnegative"),
        sa.CheckConstraint(_in_constraint("state", STEP_STATES), name="ck_steps_state"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_steps_run_ordinal"),
    )
    op.create_table(
        "attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("steps.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("failure_kind", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_number > 0", name="ck_attempts_number_positive"),
        sa.CheckConstraint(_in_constraint("state", ATTEMPT_STATES), name="ck_attempts_state"),
        sa.CheckConstraint("failure_kind IS NULL OR " + _in_constraint("failure_kind", FAILURE_KINDS), name="ck_attempts_failure_kind"),
        sa.UniqueConstraint("step_id", "attempt_number", name="uq_attempts_step_number"),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="ck_artifacts_size_nonnegative"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_artifacts_sha256"),
        sa.UniqueConstraint("run_id", "path", name="uq_artifacts_run_path"),
    )
    op.create_table(
        "workflow_start_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_id", sa.String(256), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_attempt_nonnegative"),
        sa.UniqueConstraint("run_id", name="uq_workflow_start_outbox_run"),
    )
    op.create_index("ix_workflow_start_outbox_pending", "workflow_start_outbox", ["available_at"], postgresql_where=sa.text("started_at IS NULL"))
    op.create_table(
        "projection_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_projection_events_sequence_nonnegative"),
        sa.UniqueConstraint("event_id", name="uq_projection_events_event_id"),
    )

    definitions = sa.table("agent_definitions", sa.column("id"), sa.column("slug"), sa.column("name"))
    versions = sa.table("agent_versions", sa.column("id"), sa.column("agent_definition_id"), sa.column("version"))
    op.bulk_insert(definitions, [{"id": DEFINITION_ID, "slug": "support-report", "name": "Support Report"}])
    op.bulk_insert(versions, [{"id": VERSION_ID, "agent_definition_id": DEFINITION_ID, "version": 1}])


def downgrade() -> None:
    op.drop_table("projection_events")
    op.drop_index("ix_workflow_start_outbox_pending", table_name="workflow_start_outbox")
    op.drop_table("workflow_start_outbox")
    op.drop_table("artifacts")
    op.drop_table("attempts")
    op.drop_table("steps")
    op.drop_table("runs")
    op.drop_table("agent_versions")
    op.drop_table("agent_definitions")
