"""Add immutable model profiles and attempt usage evidence.

Revision ID: 0003_model_profiles_and_usage
Revises: 0002_approval_and_signal_records
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from agentforge.model_profiles import MOCK_MODEL_PROFILE


revision = "0003_model_profiles_and_usage"
down_revision = "0002_approval_and_signal_records"
branch_labels = None
depends_on = None

MOCK_PROFILE = MOCK_MODEL_PROFILE.model_dump(mode="json")
MOCK_PROFILE_JSON = json.dumps(MOCK_PROFILE, sort_keys=True, separators=(",", ":"))
MOCK_PROFILE_SQL_JSON = MOCK_PROFILE_JSON.replace(":", r"\:")


def _hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _recompute_profile_hashes() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, request_payload, model_profile FROM runs")
    ).mappings()
    for row in rows:
        request_hash = _hash(
            {
                "request": row["request_payload"],
                "model_profile": row["model_profile"],
            }
        )
        connection.execute(
            sa.text("UPDATE runs SET request_hash = :request_hash WHERE id = :id"),
            {"id": row["id"], "request_hash": request_hash},
        )


def _recompute_legacy_hashes() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, request_payload FROM runs")
    ).mappings()
    for row in rows:
        connection.execute(
            sa.text("UPDATE runs SET request_hash = :request_hash WHERE id = :id"),
            {"id": row["id"], "request_hash": _hash(row["request_payload"])},
        )


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "model_profile",
            postgresql.JSONB(),
            server_default=sa.text(f"'{MOCK_PROFILE_SQL_JSON}'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE workflow_start_outbox SET payload = payload || "
            "jsonb_build_object('model_profile', CAST(:profile AS JSONB))"
        ).bindparams(profile=MOCK_PROFILE_JSON)
    )
    _recompute_profile_hashes()

    op.add_column("attempts", sa.Column("model_provider", sa.String(32)))
    op.add_column("attempts", sa.Column("model_name", sa.String(256)))
    op.add_column("attempts", sa.Column("model_digest", sa.String(64)))
    op.add_column("attempts", sa.Column("model_request_count", sa.Integer()))
    op.add_column("attempts", sa.Column("model_input_tokens", sa.Integer()))
    op.add_column("attempts", sa.Column("model_output_tokens", sa.Integer()))
    op.add_column("attempts", sa.Column("model_duration_ms", sa.BigInteger()))
    op.add_column(
        "attempts", sa.Column("provider_cost_estimate_usd", sa.Numeric(18, 6))
    )
    op.add_column("attempts", sa.Column("input_tokens_estimated", sa.Boolean()))
    op.add_column("attempts", sa.Column("usage_uncertain", sa.Boolean()))
    op.create_check_constraint(
        "ck_attempts_model_provider",
        "attempts",
        "model_provider IS NULL OR model_provider IN ('mock', 'ollama')",
    )
    op.create_check_constraint(
        "ck_attempts_model_digest_sha256",
        "attempts",
        "model_digest IS NULL OR model_digest ~ '^[0-9a-f]{64}$'",
    )
    for column in (
        "model_request_count",
        "model_input_tokens",
        "model_output_tokens",
        "model_duration_ms",
    ):
        op.create_check_constraint(
            f"ck_attempts_{column}_nonnegative",
            "attempts",
            f"{column} IS NULL OR {column} >= 0",
        )
    op.create_check_constraint(
        "ck_attempts_provider_cost_nonnegative",
        "attempts",
        "provider_cost_estimate_usd IS NULL OR provider_cost_estimate_usd >= 0",
    )


def downgrade() -> None:
    _recompute_legacy_hashes()
    op.execute(
        sa.text("UPDATE workflow_start_outbox SET payload = payload - 'model_profile'")
    )
    op.drop_constraint(
        "ck_attempts_provider_cost_nonnegative", "attempts", type_="check"
    )
    for column in reversed(
        (
            "model_request_count",
            "model_input_tokens",
            "model_output_tokens",
            "model_duration_ms",
        )
    ):
        op.drop_constraint(
            f"ck_attempts_{column}_nonnegative", "attempts", type_="check"
        )
    op.drop_constraint(
        "ck_attempts_model_digest_sha256", "attempts", type_="check"
    )
    op.drop_constraint("ck_attempts_model_provider", "attempts", type_="check")
    for column in (
        "usage_uncertain",
        "input_tokens_estimated",
        "provider_cost_estimate_usd",
        "model_duration_ms",
        "model_output_tokens",
        "model_input_tokens",
        "model_request_count",
        "model_digest",
        "model_name",
        "model_provider",
    ):
        op.drop_column("attempts", column)
    op.drop_column("runs", "model_profile")
