from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from agentforge.config import InboxSettings
from agentforge.domain import ArtifactRef, DeliveryRequest


CONTENT = "# Support Report\n"
CONTENT_SHA256 = "86f80670834fa52bc574e02c7deea10c6243b3058d44ff9f8dbfebe7fa18a5f5"
CHANGED_CONTENT = "# Changed\n"
CHANGED_SHA256 = "fa8549bc791b513f06435d4e2b912b37bfed2e8388ad5edd89c33a9fee467f7a"
ACTION_HASH = "b" * 64


def _inbox_modules():
    try:
        api = importlib.import_module("agentforge.inbox_api")
        models = importlib.import_module("agentforge.inbox_db_models")
        repository = importlib.import_module("agentforge.inbox_repository")
    except ModuleNotFoundError:
        pytest.fail("simulated inbox modules do not exist")
    return api, models, repository


def request_payload(
    operation_id: UUID | None = None,
    *,
    changed: bool = False,
) -> dict[str, object]:
    content = CHANGED_CONTENT if changed else CONTENT
    sha256 = CHANGED_SHA256 if changed else CONTENT_SHA256
    return {
        "operation_id": str(operation_id or uuid4()),
        "action_hash": ACTION_HASH,
        "artifact": {
            "path": "reports/run/support-report.md",
            "sha256": sha256,
            "size_bytes": len(content.encode("utf-8")),
            "media_type": "text/markdown",
        },
        "content": content,
    }


@asynccontextmanager
async def inbox_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client


@pytest.mark.integration
async def test_receiver_returns_original_receipt_and_conflicts_on_changed_content(
    inbox_database_engine: AsyncEngine, inbox_test_database_url: str
) -> None:
    api, _, _ = _inbox_modules()
    app = api.create_inbox_app(InboxSettings(database_url=inbox_test_database_url))
    operation_id = uuid4()
    payload = request_payload(operation_id)

    async with inbox_client(app) as client:
        first = await client.post("/api/v1/deliveries", json=payload)
        duplicate = await client.post("/api/v1/deliveries", json=payload)
        conflict = await client.post(
            "/api/v1/deliveries", json=request_payload(operation_id, changed=True)
        )
        fetched = await client.get(f"/api/v1/deliveries/{operation_id}")
        missing = await client.get(f"/api/v1/deliveries/{uuid4()}")

    assert first.status_code == 201
    assert duplicate.status_code == 200
    assert duplicate.content == first.content
    assert conflict.status_code == 409
    assert "database" not in conflict.text.lower()
    assert fetched.json() == first.json()
    assert missing.status_code == 404


@pytest.mark.integration
async def test_receiver_rejects_content_that_does_not_match_artifact_metadata(
    inbox_database_engine: AsyncEngine, inbox_test_database_url: str
) -> None:
    api, _, _ = _inbox_modules()
    app = api.create_inbox_app(InboxSettings(database_url=inbox_test_database_url))
    payload = request_payload()
    payload["content"] = "tampered"

    async with inbox_client(app) as client:
        response = await client.post("/api/v1/deliveries", json=payload)

    assert response.status_code == 422
    assert "database" not in response.text.lower()


@pytest.mark.integration
async def test_concurrent_receiver_attempts_commit_one_delivery(
    inbox_database_engine: AsyncEngine,
) -> None:
    _, models, repository = _inbox_modules()
    sessions = async_sessionmaker(inbox_database_engine, expire_on_commit=False)
    operation_id = uuid4()
    request = DeliveryRequest.model_validate(request_payload(operation_id))

    async def deliver_once(candidate: DeliveryRequest):
        async with sessions() as session:
            return await repository.InboxRepository.deliver(session, candidate)

    first, second = await asyncio.gather(deliver_once(request), deliver_once(request))

    assert first.receipt == second.receipt
    assert {first.created, second.created} == {True, False}
    async with sessions() as session:
        count = await session.scalar(select(func.count()).select_from(models.InboxDeliveryRecord))
    assert count == 1


@pytest.mark.integration
async def test_concurrent_changed_payload_preserves_one_winner(
    inbox_database_engine: AsyncEngine,
) -> None:
    _, models, repository = _inbox_modules()
    sessions = async_sessionmaker(inbox_database_engine, expire_on_commit=False)
    operation_id = uuid4()
    original = DeliveryRequest.model_validate(request_payload(operation_id))
    changed = DeliveryRequest.model_validate(
        request_payload(operation_id, changed=True)
    )

    async def capture(candidate: DeliveryRequest):
        try:
            async with sessions() as session:
                return await repository.InboxRepository.deliver(session, candidate)
        except repository.InboxDeliveryConflict as error:
            return error

    results = await asyncio.gather(capture(original), capture(changed))

    assert sum(isinstance(item, repository.InboxDeliveryResult) for item in results) == 1
    assert sum(isinstance(item, repository.InboxDeliveryConflict) for item in results) == 1
    async with sessions() as session:
        records = list((await session.scalars(select(models.InboxDeliveryRecord))).all())
    assert len(records) == 1
