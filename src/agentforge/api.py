"""Bounded HTTP API for AgentForge run submission and status."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from agentforge.config import Settings
from agentforge.database import create_engine_and_sessionmaker
from agentforge.db_models import AgentDefinitionRecord, AgentVersionRecord
from agentforge.domain import (
    ApiErrorResponse,
    ApprovalDecisionRequest,
    ApprovalSnapshot,
    ArtifactContent,
    CancellationSnapshot,
    ModelProfileDescriptor,
    RunDetailSnapshot,
    RunListItem,
    RunSnapshot,
    SubmitRun,
)
from agentforge.multi_agent import SUPPORT_REPORT_GRAPH_V2
from agentforge.repository import (
    ApprovalConflict,
    ApprovalNotFound,
    ApprovalRepository,
    CancellationConflict,
    CancellationRepository,
    GraphLimitExceeded,
    RunRepository,
    SubmissionConflict,
)
from agentforge.storage import ArtifactStorage, StorageViolation


_NOT_FOUND_RESPONSE = {
    "model": ApiErrorResponse,
    "description": "The requested resource was not found.",
}
_CONFLICT_RESPONSE = {
    "model": ApiErrorResponse,
    "description": "The request conflicts with durable state.",
}
_VALIDATION_RESPONSE = {
    "model": ApiErrorResponse,
    "description": "The request failed validation.",
}
_UNAVAILABLE_RESPONSE = {
    "model": ApiErrorResponse,
    "description": "A required local service is unavailable.",
}


def create_app(settings: Settings) -> FastAPI:
    engine, sessions = create_engine_and_sessionmaker(settings)
    storage = ArtifactStorage(
        ticket_root=settings.ticket_root,
        artifact_root=settings.artifact_root,
        max_ticket_bytes=settings.max_ticket_bytes,
        max_total_ticket_bytes=settings.max_total_ticket_bytes,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title="AgentForge API", version="0.1.0", lifespan=lifespan)

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
        "/api/v1/runs",
        response_model=RunSnapshot,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            status.HTTP_200_OK: {
                "model": RunSnapshot,
                "description": "The exact submission already exists.",
            },
            status.HTTP_409_CONFLICT: _CONFLICT_RESPONSE,
            status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_RESPONSE,
            status.HTTP_503_SERVICE_UNAVAILABLE: _UNAVAILABLE_RESPONSE,
        },
    )
    async def submit_run(request: SubmitRun, response: Response) -> RunSnapshot:
        try:
            try:
                model_profile = settings.resolve_model_profile(
                    request.model_profile_id
                )
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="model profile is not enabled",
                ) from None
            async with sessions() as session:
                agent_version_id = await session.scalar(
                    select(AgentVersionRecord.id)
                    .join(
                        AgentDefinitionRecord,
                        AgentDefinitionRecord.id
                        == AgentVersionRecord.agent_definition_id,
                    )
                    .where(
                        AgentDefinitionRecord.slug == "support-report",
                        AgentVersionRecord.version == 2,
                    )
                )
            if agent_version_id is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="service unavailable",
                )
            async with sessions() as session:
                result = await RunRepository.submit(
                    session,
                    request,
                    agent_version_id=agent_version_id,
                    model_profile=model_profile,
                    workflow_kind="support-report-v2",
                    graph_limits=SUPPORT_REPORT_GRAPH_V2,
                )
        except GraphLimitExceeded as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from None
        except SubmissionConflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="submission key already belongs to a different request",
            ) from None
        except HTTPException:
            raise
        except (SQLAlchemyError, OSError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from None

        if not result.created:
            response.status_code = status.HTTP_200_OK
        return result.run

    @app.get(
        "/api/v1/model-profiles", response_model=list[ModelProfileDescriptor]
    )
    async def list_model_profiles() -> tuple[ModelProfileDescriptor, ...]:
        return settings.model_profile_descriptors()

    @app.get(
        "/api/v1/runs",
        response_model=list[RunListItem],
        responses={
            status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_RESPONSE,
            status.HTTP_503_SERVICE_UNAVAILABLE: _UNAVAILABLE_RESPONSE,
        },
    )
    async def list_runs(
        limit: int = Query(default=20, ge=1, le=50)
    ) -> tuple[RunListItem, ...]:
        try:
            async with sessions() as session:
                return await RunRepository.list_recent(session, limit)
        except (SQLAlchemyError, OSError, ValidationError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from None

    @app.get(
        "/api/v1/runs/{run_id}/detail",
        response_model=RunDetailSnapshot,
        responses={
            status.HTTP_404_NOT_FOUND: _NOT_FOUND_RESPONSE,
            status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_RESPONSE,
            status.HTTP_503_SERVICE_UNAVAILABLE: _UNAVAILABLE_RESPONSE,
        },
    )
    async def get_run_detail(run_id: UUID) -> RunDetailSnapshot:
        try:
            async with sessions() as session:
                detail = await RunRepository.get_detail(session, run_id)
        except (SQLAlchemyError, OSError, ValidationError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from None
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="run not found"
            )
        return detail

    @app.get(
        "/api/v1/runs/{run_id}/artifact",
        response_model=ArtifactContent,
        responses={
            status.HTTP_404_NOT_FOUND: _NOT_FOUND_RESPONSE,
            status.HTTP_409_CONFLICT: _CONFLICT_RESPONSE,
            status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_RESPONSE,
            status.HTTP_503_SERVICE_UNAVAILABLE: _UNAVAILABLE_RESPONSE,
        },
    )
    async def get_artifact_content(run_id: UUID) -> ArtifactContent:
        try:
            async with sessions() as session:
                run = await RunRepository.get(session, run_id)
            if run is None or run.artifact is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="artifact not found",
                )
            if run.artifact.media_type != "text/markdown":
                raise StorageViolation("artifact media type is not approved")
            content = storage.read_verified_artifact(run.artifact).decode(
                "utf-8", errors="strict"
            )
            return ArtifactContent(artifact=run.artifact, content=content)
        except HTTPException:
            raise
        except (StorageViolation, UnicodeDecodeError, ValidationError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="artifact could not be verified",
            ) from None
        except (SQLAlchemyError, OSError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from None

    @app.get(
        "/api/v1/runs/{run_id}",
        response_model=RunSnapshot,
        responses={
            status.HTTP_404_NOT_FOUND: _NOT_FOUND_RESPONSE,
            status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_RESPONSE,
            status.HTTP_503_SERVICE_UNAVAILABLE: _UNAVAILABLE_RESPONSE,
        },
    )
    async def get_run(run_id: UUID) -> RunSnapshot:
        try:
            async with sessions() as session:
                snapshot = await RunRepository.get(session, run_id)
        except (SQLAlchemyError, OSError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from None
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="run not found"
            )
        return snapshot

    @app.get(
        "/api/v1/runs/{run_id}/approval",
        response_model=ApprovalSnapshot,
        responses={
            status.HTTP_404_NOT_FOUND: _NOT_FOUND_RESPONSE,
            status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_RESPONSE,
            status.HTTP_503_SERVICE_UNAVAILABLE: _UNAVAILABLE_RESPONSE,
        },
    )
    async def get_approval(run_id: UUID) -> ApprovalSnapshot:
        try:
            async with sessions() as session:
                approval = await ApprovalRepository.get_for_run(session, run_id)
        except (SQLAlchemyError, OSError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from None
        if approval is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="approval not found",
            )
        return approval

    @app.post(
        "/api/v1/approvals/{approval_id}/decision",
        response_model=ApprovalSnapshot,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            status.HTTP_200_OK: {
                "model": ApprovalSnapshot,
                "description": "The exact decision already exists.",
            },
            status.HTTP_404_NOT_FOUND: _NOT_FOUND_RESPONSE,
            status.HTTP_409_CONFLICT: _CONFLICT_RESPONSE,
            status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_RESPONSE,
            status.HTTP_503_SERVICE_UNAVAILABLE: _UNAVAILABLE_RESPONSE,
        },
    )
    async def decide_approval(
        approval_id: UUID,
        request: ApprovalDecisionRequest,
        response: Response,
    ) -> ApprovalSnapshot:
        try:
            async with sessions() as session:
                result = await ApprovalRepository.decide(
                    session, approval_id, request
                )
        except ApprovalNotFound:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="approval not found",
            ) from None
        except ApprovalConflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="approval decision conflicts with durable state",
            ) from None
        except (SQLAlchemyError, OSError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from None
        if not result.created:
            response.status_code = status.HTTP_200_OK
        return result.approval

    @app.post(
        "/api/v1/runs/{run_id}/cancel",
        response_model=CancellationSnapshot,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            status.HTTP_200_OK: {
                "model": CancellationSnapshot,
                "description": "The cancellation request already exists.",
            },
            status.HTTP_404_NOT_FOUND: _NOT_FOUND_RESPONSE,
            status.HTTP_409_CONFLICT: _CONFLICT_RESPONSE,
            status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_RESPONSE,
            status.HTTP_503_SERVICE_UNAVAILABLE: _UNAVAILABLE_RESPONSE,
        },
    )
    async def cancel_run(run_id: UUID, response: Response) -> CancellationSnapshot:
        try:
            async with sessions() as session:
                run = await RunRepository.get(session, run_id)
            if run is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="run not found",
                )
            async with sessions() as session:
                result = await CancellationRepository.request(session, run_id)
        except CancellationConflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="run cannot be cancelled in its durable state",
            ) from None
        except HTTPException:
            raise
        except (SQLAlchemyError, OSError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service unavailable",
            ) from None
        if not result.created:
            response.status_code = status.HTTP_200_OK
        return result.cancellation

    return app


def main() -> None:
    """Run the local-only API on a psycopg-compatible Windows event loop."""

    settings = Settings.from_env()
    config = uvicorn.Config(create_app(settings), host="127.0.0.1", port=8000)
    server = uvicorn.Server(config)
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
