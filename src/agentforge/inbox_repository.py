"""Durable idempotency boundary owned by the simulated inbox."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.domain import DeliveryReceipt, DeliveryRequest
from agentforge.inbox_db_models import InboxDeliveryRecord


class InboxDeliveryConflict(Exception):
    """An operation ID already belongs to different canonical content."""


class InboxDeliveryRejected(ValueError):
    """The report content does not match its trusted artifact metadata."""


@dataclass(frozen=True)
class InboxDeliveryResult:
    receipt: DeliveryReceipt
    created: bool


def _request_hash(request: DeliveryRequest) -> str:
    encoded = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_content(request: DeliveryRequest) -> None:
    content = request.content.encode("utf-8")
    if (
        request.artifact.media_type != "text/markdown"
        or len(content) != request.artifact.size_bytes
        or hashlib.sha256(content).hexdigest() != request.artifact.sha256
    ):
        raise InboxDeliveryRejected("content does not match artifact metadata")


def _receipt(record: InboxDeliveryRecord) -> DeliveryReceipt:
    return DeliveryReceipt(
        delivery_id=record.receiver_delivery_id,
        operation_id=record.operation_id,
        artifact_sha256=record.artifact_sha256,
        committed_at=record.committed_at,
    )


class InboxRepository:
    @staticmethod
    async def deliver(
        session: AsyncSession,
        request: DeliveryRequest,
        *,
        now: datetime | None = None,
    ) -> InboxDeliveryResult:
        _validate_content(request)
        request_hash = _request_hash(request)
        committed_at = now or datetime.now(UTC)
        receiver_delivery_id = uuid4()
        async with session.begin():
            inserted = await session.scalar(
                insert(InboxDeliveryRecord)
                .values(
                    operation_id=request.operation_id,
                    request_hash=request_hash,
                    action_hash=request.action_hash,
                    artifact_path=request.artifact.path,
                    artifact_media_type=request.artifact.media_type,
                    artifact_size_bytes=request.artifact.size_bytes,
                    artifact_sha256=request.artifact.sha256,
                    content=request.content,
                    receiver_delivery_id=receiver_delivery_id,
                    committed_at=committed_at,
                )
                .on_conflict_do_nothing(index_elements=["operation_id"])
                .returning(InboxDeliveryRecord.operation_id)
            )
            record = await session.get(InboxDeliveryRecord, request.operation_id)
            if record is None:  # pragma: no cover - insert/read share a transaction
                raise RuntimeError("inbox delivery could not be read after insert")
            if record.request_hash != request_hash:
                raise InboxDeliveryConflict(str(request.operation_id))
            receipt = _receipt(record)
        return InboxDeliveryResult(receipt=receipt, created=inserted is not None)

    @staticmethod
    async def get(
        session: AsyncSession, operation_id: UUID
    ) -> DeliveryReceipt | None:
        record = await session.scalar(
            select(InboxDeliveryRecord).where(
                InboxDeliveryRecord.operation_id == operation_id
            )
        )
        return _receipt(record) if record else None
