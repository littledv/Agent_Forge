from __future__ import annotations

from typing import Literal

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


DatabaseKind = Literal["application", "inbox"]

_CLEANUP_SQL: dict[DatabaseKind, str] = {
    "application": (
        "TRUNCATE workflow_signal_outbox, deliveries, cancellation_requests, "
        "approvals, projection_events, workflow_start_outbox, artifacts, attempts, "
        "steps, runs CASCADE"
    ),
    "inbox": "TRUNCATE deliveries",
}


def require_disposable_database_url(database_url: str) -> URL:
    try:
        parsed = make_url(database_url)
    except ArgumentError as exc:
        raise ValueError("disposable database URL is malformed") from exc

    if parsed.drivername != "postgresql+psycopg":
        raise ValueError("disposable database requires the postgresql+psycopg driver")
    if parsed.query:
        raise ValueError("disposable database URL must not contain query parameters")
    if parsed.host not in {"127.0.0.1", "localhost"}:
        raise ValueError("disposable database requires a loopback host")
    if not parsed.database or not parsed.database.endswith("_test"):
        raise ValueError("disposable database name must end in _test")
    return parsed


def reset_disposable_database(
    config: Config, database_url: str, *, kind: DatabaseKind
) -> None:
    parsed = require_disposable_database_url(database_url)
    configured_url = config.get_main_option("sqlalchemy.url")
    try:
        configured = require_disposable_database_url(configured_url or "")
    except ValueError as exc:
        raise ValueError(
            "configured target is not the validated disposable database"
        ) from exc
    if configured != parsed:
        raise ValueError("configured target does not match disposable database URL")
    try:
        cleanup_sql = _CLEANUP_SQL[kind]
    except KeyError as exc:
        raise ValueError("unsupported disposable database kind") from exc

    command.upgrade(config, "head")
    engine = create_engine(parsed)
    try:
        with engine.begin() as connection:
            connection.execute(text(cleanup_sql))
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    finally:
        engine.dispose()
