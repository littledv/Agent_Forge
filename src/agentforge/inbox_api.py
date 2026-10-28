"""Loopback-only HTTP API for the independently persisted simulated inbox."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agentforge.config import InboxSettings
from agentforge.domain import DeliveryReceipt, DeliveryRequest
from agentforge.inbox_repository import (
    InboxDeliveryConflict,
    InboxDeliveryRejected,
    InboxRepository,
)


def create_inbox_app(settings: InboxSettings) -> FastAPI:
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title="AgentForge Simulated Inbox", version="0.1.0", lifespan=lifespan)

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness() -> dict[str, str]:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from None
        return {"status": "ready"}

    @app.post(
        "/api/v1/deliveries",
        response_model=DeliveryReceipt,
        status_code=status.HTTP_201_CREATED,
    )
    async def deliver(request: DeliveryRequest, response: Response) -> DeliveryReceipt:
        try:
            async with sessions() as session:
                result = await InboxRepository.deliver(session, request)
        except InboxDeliveryConflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="operation ID already belongs to different content",
            ) from None
        except InboxDeliveryRejected:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="content does not match artifact metadata",
            ) from None
        except (SQLAlchemyError, OSError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from None
        if not result.created:
            response.status_code = status.HTTP_200_OK
        return result.receipt

    @app.get("/api/v1/deliveries/{operation_id}", response_model=DeliveryReceipt)
    async def get_delivery(operation_id: UUID) -> DeliveryReceipt:
        try:
            async with sessions() as session:
                receipt = await InboxRepository.get(session, operation_id)
        except (SQLAlchemyError, OSError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from None
        if receipt is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="delivery not found",
            )
        return receipt

    return app


def main() -> None:
    settings = InboxSettings.from_env()
    config = uvicorn.Config(create_inbox_app(settings), host="127.0.0.1", port=8001)
    server = uvicorn.Server(config)
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
