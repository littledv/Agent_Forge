"""SQLAlchemy mappings owned exclusively by the simulated inbox."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class InboxBase(DeclarativeBase):
    pass


class InboxDeliveryRecord(InboxBase):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint(
            "receiver_delivery_id", name="uq_inbox_deliveries_receiver_id"
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_inbox_deliveries_request_hash",
        ),
        CheckConstraint(
            "action_hash ~ '^[0-9a-f]{64}$'",
            name="ck_inbox_deliveries_action_hash",
        ),
        CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_inbox_deliveries_artifact_sha256",
        ),
        CheckConstraint(
            "artifact_size_bytes >= 0",
            name="ck_inbox_deliveries_artifact_size_nonnegative",
        ),
    )

    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(512), nullable=False)
    artifact_media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_size_bytes: Mapped[int] = mapped_column(nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    receiver_delivery_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
