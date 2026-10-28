"""Native Worker subprocess used only by the recovery integration test."""

from __future__ import annotations

import asyncio
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

from agentforge.config import Settings
from agentforge.domain import (
    DeliveryReceipt,
    DeliveryRequest,
    TicketSnapshotRef,
)
from agentforge.storage import ArtifactStorage
from agentforge.worker import create_worker


class BlockingSnapshotStorage(ArtifactStorage):
    def __init__(
        self,
        *,
        ticket_root: Path,
        artifact_root: Path,
        max_ticket_bytes: int,
        max_total_ticket_bytes: int,
        marker_path: Path,
    ) -> None:
        super().__init__(
            ticket_root=ticket_root,
            artifact_root=artifact_root,
            max_ticket_bytes=max_ticket_bytes,
            max_total_ticket_bytes=max_total_ticket_bytes,
        )
        self._marker_path = marker_path

    def snapshot_tickets(
        self, run_id: UUID, ticket_files: tuple[str, ...]
    ) -> TicketSnapshotRef:
        self._marker_path.write_text("running\n", encoding="utf-8")
        threading.Event().wait()
        return super().snapshot_tickets(run_id, ticket_files)


class DeterministicRecoveryInbox:
    def deliver(self, request: DeliveryRequest) -> DeliveryReceipt:
        return DeliveryReceipt(
            delivery_id=uuid5(request.operation_id, "recovery-receiver"),
            operation_id=request.operation_id,
            artifact_sha256=request.artifact.sha256,
            committed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


async def run(settings: Settings) -> None:
    marker_value = os.environ.get("AGENTFORGE_RECOVERY_BLOCK_MARKER")
    if marker_value is None:
        worker = await create_worker(
            settings, inbox_client=DeterministicRecoveryInbox()
        )
    else:
        storage = BlockingSnapshotStorage(
            ticket_root=settings.ticket_root,
            artifact_root=settings.artifact_root,
            max_ticket_bytes=settings.max_ticket_bytes,
            max_total_ticket_bytes=settings.max_total_ticket_bytes,
            marker_path=Path(marker_value),
        )
        worker = await create_worker(
            settings,
            storage=storage,
            inbox_client=DeterministicRecoveryInbox(),
        )
    await worker.run()


def main() -> None:
    asyncio.run(
        run(Settings.from_env()), loop_factory=asyncio.SelectorEventLoop
    )


if __name__ == "__main__":
    main()
