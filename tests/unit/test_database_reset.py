from __future__ import annotations

from collections.abc import Callable

import pytest
from alembic.config import Config

import tests.helpers.database_reset as database_reset


DEFAULT_APPLICATION_URL = (
    "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:5432/"
    "agentforge_test"
)
DEFAULT_INBOX_URL = (
    "postgresql+psycopg://temporal:temporal-local-only@localhost:5432/"
    "agentforge_inbox_test"
)


def config_for(database_url: str) -> Config:
    config = Config()
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.mark.parametrize(
    ("database_url", "expected_database"),
    [
        (DEFAULT_APPLICATION_URL, "agentforge_test"),
        (DEFAULT_INBOX_URL, "agentforge_inbox_test"),
    ],
)
def test_require_disposable_database_url_accepts_only_local_psycopg_test_database(
    database_url: str, expected_database: str
) -> None:
    parsed = database_reset.require_disposable_database_url(database_url)

    assert parsed.drivername == "postgresql+psycopg"
    assert parsed.host in {"127.0.0.1", "localhost"}
    assert parsed.database == expected_database


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://user:secret-password@127.0.0.1/agentforge",
        "postgresql+psycopg://user:secret-password@127.0.0.1/agentforge_inbox",
        "postgresql+psycopg://user:secret-password@127.0.0.1/agentforge_staging",
        "postgresql+psycopg://user:secret-password@database.internal/agentforge_test",
        (
            "postgresql+psycopg://user:secret-password@127.0.0.1/agentforge_test"
            "?dbname=agentforge&host=192.0.2.1"
        ),
        (
            "postgresql+psycopg://user:secret-password@127.0.0.1/agentforge_test"
            "?service=production"
        ),
        "sqlite:///agentforge_test",
        "postgresql+psycopg://user:secret-password@localhost:5432",
        "not a url://secret-password",
    ],
)
def test_require_disposable_database_url_rejects_unsafe_targets_without_echoing_url(
    database_url: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        database_reset.require_disposable_database_url(database_url)

    message = str(exc_info.value)
    assert database_url not in message
    assert "secret-password" not in message


@pytest.mark.parametrize(
    ("kind", "expected_sql"),
    [
        (
            "application",
            "TRUNCATE workflow_signal_outbox, deliveries, cancellation_requests, "
            "approvals, projection_events, workflow_start_outbox, artifacts, attempts, "
            "steps, runs CASCADE",
        ),
        ("inbox", "TRUNCATE deliveries"),
    ],
)
def test_reset_disposable_database_runs_upgrade_cleanup_downgrade_upgrade_in_order(
    monkeypatch: pytest.MonkeyPatch, kind: database_reset.DatabaseKind, expected_sql: str
) -> None:
    events: list[str] = []

    class FakeConnection:
        def execute(self, statement: object) -> None:
            events.append(f"sql:{statement}")

    class FakeTransaction:
        def __enter__(self) -> FakeConnection:
            return FakeConnection()

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeEngine:
        def begin(self) -> FakeTransaction:
            return FakeTransaction()

        def dispose(self) -> None:
            events.append("dispose")

    monkeypatch.setattr(
        database_reset.command,
        "upgrade",
        lambda _config, revision: events.append(f"upgrade:{revision}"),
    )
    monkeypatch.setattr(
        database_reset.command,
        "downgrade",
        lambda _config, revision: events.append(f"downgrade:{revision}"),
    )
    monkeypatch.setattr(database_reset, "create_engine", lambda _url: FakeEngine())

    database_reset.reset_disposable_database(
        config_for(DEFAULT_APPLICATION_URL), DEFAULT_APPLICATION_URL, kind=kind
    )

    assert events == [
        "upgrade:head",
        f"sql:{expected_sql}",
        "downgrade:base",
        "upgrade:head",
        "dispose",
    ]


def test_reset_disposable_database_refuses_before_alembic_or_engine_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def record(name: str) -> Callable[..., None]:
        return lambda *_args, **_kwargs: calls.append(name)

    monkeypatch.setattr(database_reset.command, "upgrade", record("upgrade"))
    monkeypatch.setattr(database_reset.command, "downgrade", record("downgrade"))
    monkeypatch.setattr(database_reset, "create_engine", record("create_engine"))

    with pytest.raises(ValueError):
        database_reset.reset_disposable_database(
            config_for(DEFAULT_APPLICATION_URL),
            "postgresql+psycopg://user:secret-password@127.0.0.1/agentforge",
            kind="application",
        )

    assert calls == []


def test_reset_disposable_database_refuses_query_override_before_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def record(name: str) -> Callable[..., None]:
        return lambda *_args, **_kwargs: calls.append(name)

    monkeypatch.setattr(database_reset.command, "upgrade", record("upgrade"))
    monkeypatch.setattr(database_reset.command, "downgrade", record("downgrade"))
    monkeypatch.setattr(database_reset, "create_engine", record("create_engine"))

    override_url = (
        f"{DEFAULT_APPLICATION_URL}?dbname=agentforge&host=192.0.2.1"
    )
    with pytest.raises(ValueError, match="query parameters"):
        database_reset.reset_disposable_database(
            config_for(override_url), override_url, kind="application"
        )

    assert calls == []


def test_reset_disposable_database_refuses_mismatched_alembic_target_before_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def record(name: str) -> Callable[..., None]:
        return lambda *_args, **_kwargs: calls.append(name)

    monkeypatch.setattr(database_reset.command, "upgrade", record("upgrade"))
    monkeypatch.setattr(database_reset.command, "downgrade", record("downgrade"))
    monkeypatch.setattr(database_reset, "create_engine", record("create_engine"))

    with pytest.raises(ValueError, match="configured target"):
        database_reset.reset_disposable_database(
            config_for(
                "postgresql+psycopg://user:secret-password@127.0.0.1/agentforge"
            ),
            DEFAULT_APPLICATION_URL,
            kind="application",
        )

    assert calls == []
