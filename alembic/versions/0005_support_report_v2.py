"""Seed the bounded support-report version 2 graph.

Revision ID: 0005_support_report_v2
Revises: 0004_model_usage_exactness
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0005_support_report_v2"
down_revision = "0004_model_usage_exactness"
branch_labels = None
depends_on = None

DEFINITION_ID = UUID("00000000-0000-0000-0000-000000000101")
VERSION_ID = UUID("00000000-0000-0000-0000-000000000103")


def _versions() -> sa.Table:
    return sa.table(
        "agent_versions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("agent_definition_id", postgresql.UUID(as_uuid=True)),
        sa.column("version", sa.Integer()),
    )


def upgrade() -> None:
    op.bulk_insert(
        _versions(),
        [{"id": VERSION_ID, "agent_definition_id": DEFINITION_ID, "version": 2}],
    )


def downgrade() -> None:
    versions = _versions()
    op.execute(versions.delete().where(versions.c.id == VERSION_ID))
