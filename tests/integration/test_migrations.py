from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from agentforge.model_profiles import MOCK_MODEL_PROFILE


EXPECTED_TABLES = {
    "agent_definitions",
    "agent_versions",
    "runs",
    "steps",
    "attempts",
    "artifacts",
    "workflow_start_outbox",
    "projection_events",
    "approvals",
    "cancellation_requests",
    "deliveries",
    "workflow_signal_outbox",
}

EXPECTED_UNIQUES = {
    "agent_definitions": {("slug",)},
    "agent_versions": {("agent_definition_id", "version")},
    "runs": {("submission_key",), ("workflow_id",)},
    "steps": {("run_id", "ordinal")},
    "attempts": {("step_id", "attempt_number")},
    "artifacts": {("run_id", "path")},
    "workflow_start_outbox": {("run_id",)},
    "projection_events": {("event_id",)},
    "approvals": {("run_id", "action_hash"), ("operation_id",)},
    "cancellation_requests": {("run_id",)},
    "deliveries": {("receiver_delivery_id",)},
    "workflow_signal_outbox": {("run_id", "signal_name", "message_id")},
}


@pytest.fixture(autouse=True)
def isolate_migration_database(test_database_url: str) -> None:
    """Remove prior test runs before exercising destructive downgrades."""

    engine = create_engine(test_database_url)
    try:
        if inspect(engine).has_table("runs"):
            with engine.begin() as connection:
                connection.execute(text("TRUNCATE runs CASCADE"))
    finally:
        engine.dispose()


@pytest.mark.integration
def test_head_seeds_support_report_versions_one_and_two(
    alembic_config: Config, test_database_url: str
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    engine = create_engine(test_database_url)
    try:
        inspector = inspect(engine)
        assert EXPECTED_TABLES <= set(inspector.get_table_names())

        for table, expected_constraints in EXPECTED_UNIQUES.items():
            actual = {
                tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(table)
            }
            if table == "projection_events":
                primary_key = tuple(inspector.get_pk_constraint(table)["constrained_columns"])
                actual.add(primary_key)
            assert expected_constraints <= actual

        with engine.connect() as connection:
            seeds = connection.execute(
                text(
                    "SELECT d.slug, v.version FROM agent_definitions d "
                    "JOIN agent_versions v ON v.agent_definition_id = d.id "
                    "ORDER BY v.version"
                )
            ).all()
        assert seeds == [("support-report", 1), ("support-report", 2)]

        command.downgrade(alembic_config, "0004_model_usage_exactness")
        with engine.connect() as connection:
            remaining = connection.execute(
                text("SELECT version FROM agent_versions ORDER BY version")
            ).scalars().all()
        assert remaining == [1]
    finally:
        engine.dispose()
        command.upgrade(alembic_config, "head")


@pytest.mark.integration
def test_safety_migration_has_constraints_and_downgrades_cleanly(
    alembic_config: Config, test_database_url: str
) -> None:
    command.upgrade(alembic_config, "head")
    engine = create_engine(test_database_url)
    try:
        inspector = inspect(engine)
        checks = {
            table: {constraint["name"] for constraint in inspector.get_check_constraints(table)}
            for table in ("approvals", "workflow_signal_outbox", "deliveries")
        }
        indexes = {index["name"] for index in inspector.get_indexes("workflow_signal_outbox")}
        foreign_keys = {
            tuple(foreign_key["constrained_columns"])
            for foreign_key in inspector.get_foreign_keys("deliveries")
        }
        assert {
            "ck_approvals_state",
            "ck_approvals_action_hash_sha256",
        } <= checks["approvals"]
        assert "ck_workflow_signal_outbox_attempt_nonnegative" in checks[
            "workflow_signal_outbox"
        ]
        assert "ck_deliveries_artifact_sha256" in checks["deliveries"]
        assert "ix_workflow_signal_outbox_pending" in indexes
        assert {("run_id",), ("approval_id",)} <= foreign_keys

        command.downgrade(alembic_config, "0001_initial_records")
        inspector = inspect(engine)
        assert not {
            "approvals",
            "cancellation_requests",
            "deliveries",
            "workflow_signal_outbox",
        } & set(inspector.get_table_names())
    finally:
        engine.dispose()
        command.upgrade(alembic_config, "head")


@pytest.mark.integration
def test_model_profile_migration_backfills_hashes_and_downgrades_cleanly(
    alembic_config: Config, test_database_url: str
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "0002_approval_and_signal_records")
    engine = create_engine(test_database_url)
    request_payload = {
        "submission_key": "migration-profile",
        "goal": "Summarize support tickets",
        "ticket_files": ["ticket-001.txt"],
    }
    legacy_hash = hashlib.sha256(
        json.dumps(request_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    run_id = uuid4()
    try:
        with engine.begin() as connection:
            version_id = connection.execute(
                text("SELECT id FROM agent_versions LIMIT 1")
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO runs "
                    "(id, agent_version_id, submission_key, request_hash, "
                    "request_payload, workflow_id, state) VALUES "
                    "(:id, :version_id, :submission_key, :request_hash, "
                    "CAST(:request_payload AS JSONB), :workflow_id, 'PENDING_START')"
                ),
                {
                    "id": run_id,
                    "version_id": version_id,
                    "submission_key": request_payload["submission_key"],
                    "request_hash": legacy_hash,
                    "request_payload": json.dumps(request_payload),
                    "workflow_id": f"agentforge/run/{run_id}",
                },
            )

        command.upgrade(alembic_config, "head")
        columns = {column["name"] for column in inspect(engine).get_columns("runs")}
        attempt_columns = {
            column["name"] for column in inspect(engine).get_columns("attempts")
        }
        with engine.connect() as connection:
            migrated = connection.execute(
                text(
                    "SELECT model_profile, request_hash FROM runs WHERE id = :id"
                ),
                {"id": run_id},
            ).one()
        expected_hash = hashlib.sha256(
            json.dumps(
                {
                    "model_profile": MOCK_MODEL_PROFILE.model_dump(mode="json"),
                    "request": request_payload,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        assert "model_profile" in columns
        assert {
            "model_provider",
            "model_name",
            "model_digest",
            "model_request_count",
            "model_exact_input_count",
            "model_input_tokens",
            "model_output_tokens",
            "model_duration_ms",
            "provider_cost_estimate_usd",
            "input_tokens_estimated",
            "usage_uncertain",
        } <= attempt_columns
        assert migrated.model_profile == MOCK_MODEL_PROFILE.model_dump(mode="json")
        assert migrated.request_hash == expected_hash

        command.downgrade(alembic_config, "0002_approval_and_signal_records")
        assert "model_profile" not in {
            column["name"] for column in inspect(engine).get_columns("runs")
        }
        with engine.connect() as connection:
            restored_hash = connection.execute(
                text("SELECT request_hash FROM runs WHERE id = :id"), {"id": run_id}
            ).scalar_one()
        assert restored_hash == legacy_hash
    finally:
        engine.dispose()
        command.upgrade(alembic_config, "head")
