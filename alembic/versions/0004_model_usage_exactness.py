"""Preserve aggregate model-usage exactness across requests.

Revision ID: 0004_model_usage_exactness
Revises: 0003_model_profiles_and_usage
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004_model_usage_exactness"
down_revision = "0003_model_profiles_and_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attempts", sa.Column("model_exact_input_count", sa.Integer())
    )
    op.execute(
        sa.text(
            "UPDATE attempts SET model_exact_input_count = CASE "
            "WHEN model_request_count IS NULL THEN NULL "
            "WHEN input_tokens_estimated = FALSE THEN model_request_count "
            "ELSE 0 END"
        )
    )
    op.create_check_constraint(
        "ck_attempts_model_exact_input_count_nonnegative",
        "attempts",
        "model_exact_input_count IS NULL OR model_exact_input_count >= 0",
    )
    op.create_check_constraint(
        "ck_attempts_model_exact_input_count_bounded",
        "attempts",
        "model_exact_input_count IS NULL OR "
        "(model_request_count IS NOT NULL AND "
        "model_exact_input_count <= model_request_count)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_attempts_model_exact_input_count_bounded", "attempts", type_="check"
    )
    op.drop_constraint(
        "ck_attempts_model_exact_input_count_nonnegative",
        "attempts",
        type_="check",
    )
    op.drop_column("attempts", "model_exact_input_count")
