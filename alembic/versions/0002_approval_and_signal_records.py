"""Add approval, control, delivery, and signal records.

Revision ID: 0002_approval_and_signal_records
Revises: 0001_initial_records
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from agentforge.db_models import APPROVAL_STATES


revision = "0002_approval_and_signal_records"
down_revision = "0001_initial_records"
branch_labels = None
depends_on = None


def _in_constraint(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_hash", sa.String(64), nullable=False),
        sa.Column("action_payload", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_in_constraint("state", APPROVAL_STATES), name="ck_approvals_state"),
        sa.CheckConstraint(
            "action_hash ~ '^[0-9a-f]{64}$'", name="ck_approvals_action_hash_sha256"
        ),
        sa.UniqueConstraint("run_id", "action_hash", name="uq_approvals_run_action_hash"),
        sa.UniqueConstraint("operation_id", name="uq_approvals_operation_id"),
    )
    op.create_table(
        "cancellation_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("run_id", name="uq_cancellation_requests_run"),
    )
    op.create_table(
        "deliveries",
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "approval_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("approvals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("receiver_delivery_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_deliveries_artifact_sha256",
        ),
        sa.UniqueConstraint("receiver_delivery_id", name="uq_deliveries_receiver_id"),
    )
    op.create_table(
        "workflow_signal_outbox",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_id", sa.String(256), nullable=False),
        sa.Column("signal_name", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_workflow_signal_outbox_attempt_nonnegative"
        ),
        sa.UniqueConstraint(
            "run_id",
            "signal_name",
            "message_id",
            name="uq_workflow_signal_outbox_message",
        ),
    )
    op.create_index(
        "ix_workflow_signal_outbox_pending",
        "workflow_signal_outbox",
        ["available_at"],
        postgresql_where=sa.text("sent_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_signal_outbox_pending", table_name="workflow_signal_outbox"
    )
    op.drop_table("workflow_signal_outbox")
    op.drop_table("deliveries")
    op.drop_table("cancellation_requests")
    op.drop_table("approvals")
