from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.helpers.database_reset import reset_disposable_database


DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:5432/agentforge_test"
)
DEFAULT_INBOX_TEST_DATABASE_URL = (
    "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:5432/"
    "agentforge_inbox_test"
)


def pytest_asyncio_loop_factories(config, item):
    """Use the Windows loop supported by psycopg's async connection."""

    return {"windows-selector": asyncio.SelectorEventLoop}


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return os.environ.get("AGENTFORGE_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def alembic_config(test_database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", test_database_url)
    return config


@pytest.fixture(scope="session")
def inbox_test_database_url() -> str:
    return os.environ.get(
        "AGENTFORGE_INBOX_TEST_DATABASE_URL", DEFAULT_INBOX_TEST_DATABASE_URL
    )


@pytest.fixture(scope="session")
def inbox_alembic_config(inbox_test_database_url: str) -> Config:
    config = Config("inbox_alembic.ini")
    config.set_main_option("sqlalchemy.url", inbox_test_database_url)
    return config


@pytest.fixture(scope="session")
def migrated_database(
    alembic_config: Config, test_database_url: str
) -> Iterator[None]:
    reset_disposable_database(
        alembic_config, test_database_url, kind="application"
    )
    yield


@pytest.fixture(scope="session")
def migrated_inbox_database(
    inbox_alembic_config: Config, inbox_test_database_url: str
) -> Iterator[None]:
    reset_disposable_database(
        inbox_alembic_config, inbox_test_database_url, kind="inbox"
    )
    yield


@pytest_asyncio.fixture
async def database_engine(
    migrated_database: None, test_database_url: str
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(test_database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE workflow_signal_outbox, deliveries, cancellation_requests, "
                "approvals, projection_events, workflow_start_outbox, artifacts, "
                "attempts, steps, runs CASCADE"
            )
        )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def inbox_database_engine(
    migrated_inbox_database: None, inbox_test_database_url: str
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(inbox_test_database_url)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE deliveries"))
    yield engine
    await engine.dispose()
