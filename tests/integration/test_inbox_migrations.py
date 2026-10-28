from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


@pytest.mark.integration
def test_inbox_migration_builds_and_removes_receiver_owned_schema(
    inbox_alembic_config: Config, inbox_test_database_url: str
) -> None:
    command.downgrade(inbox_alembic_config, "base")
    command.upgrade(inbox_alembic_config, "head")
    engine = create_engine(inbox_test_database_url)
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == {"alembic_version", "deliveries"}
        columns = {column["name"] for column in inspector.get_columns("deliveries")}
        assert columns == {
            "operation_id",
            "request_hash",
            "action_hash",
            "artifact_path",
            "artifact_media_type",
            "artifact_size_bytes",
            "artifact_sha256",
            "content",
            "receiver_delivery_id",
            "committed_at",
        }
        uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("deliveries")
        }
        primary = tuple(
            inspector.get_pk_constraint("deliveries")["constrained_columns"]
        )
        assert primary == ("operation_id",)
        assert ("receiver_delivery_id",) in uniques
        checks = {
            item["name"] for item in inspector.get_check_constraints("deliveries")
        }
        assert {
            "ck_inbox_deliveries_request_hash",
            "ck_inbox_deliveries_action_hash",
            "ck_inbox_deliveries_artifact_sha256",
            "ck_inbox_deliveries_artifact_size_nonnegative",
        } <= checks

        command.downgrade(inbox_alembic_config, "base")
        assert "deliveries" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
        command.upgrade(inbox_alembic_config, "head")
