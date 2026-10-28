"""Create simulated-inbox delivery records.

Revision ID: 0001_inbox_deliveries
Revises: None
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0001_inbox_deliveries"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deliveries",
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("action_hash", sa.String(64), nullable=False),
        sa.Column("artifact_path", sa.String(512), nullable=False),
        sa.Column("artifact_media_type", sa.String(128), nullable=False),
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("receiver_delivery_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_inbox_deliveries_request_hash",
        ),
        sa.CheckConstraint(
            "action_hash ~ '^[0-9a-f]{64}$'",
            name="ck_inbox_deliveries_action_hash",
        ),
        sa.CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_inbox_deliveries_artifact_sha256",
        ),
        sa.CheckConstraint(
            "artifact_size_bytes >= 0",
            name="ck_inbox_deliveries_artifact_size_nonnegative",
        ),
        sa.UniqueConstraint(
            "receiver_delivery_id", name="uq_inbox_deliveries_receiver_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("deliveries")
