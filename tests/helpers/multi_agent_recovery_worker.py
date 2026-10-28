"""Worker subprocess used to prove bounded v2 role recovery."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from agentforge.config import Settings
from agentforge.mock_model import DeterministicMockModel
from agentforge.model_runtime import ModelUsageReporter
from agentforge.role_runtime import (
    ReportBody,
    ResearchNoteBody,
    ResearchRoleRequest,
    ReviewBody,
    ReviewerRoleRequest,
    WriterRoleRequest,
)
from agentforge.storage import ArtifactStorage
from agentforge.worker import create_worker


class BlockingReviewer:
    def __init__(
        self,
        delegate: DeterministicMockModel,
        marker_path: Path,
        identity_token: str,
    ) -> None:
        self._delegate = delegate
        self._marker_path = marker_path
        self._identity_token = identity_token

    async def research(
        self, request: ResearchRoleRequest, usage: ModelUsageReporter
    ) -> ResearchNoteBody:
        return await self._delegate.research(request, usage)

    async def write(
        self, request: WriterRoleRequest, usage: ModelUsageReporter
    ) -> ReportBody:
        return await self._delegate.write(request, usage)

    async def review(
        self, request: ReviewerRoleRequest, usage: ModelUsageReporter
    ) -> ReviewBody:
        if request.review_round == 0:
            payload = {
                "pid": os.getpid(),
                "identity_token": self._identity_token,
                "started_at_utc": datetime.now(UTC).isoformat(),
            }
            temporary = self._marker_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(temporary, self._marker_path)
            await asyncio.Event().wait()
        return await self._delegate.review(request, usage)


def build_storage(settings: Settings) -> ArtifactStorage:
    return ArtifactStorage(
        ticket_root=settings.ticket_root,
        artifact_root=settings.artifact_root,
        max_ticket_bytes=settings.max_ticket_bytes,
        max_total_ticket_bytes=settings.max_total_ticket_bytes,
    )


async def run(settings: Settings) -> None:
    storage = build_storage(settings)
    marker_value = os.environ.get("AGENTFORGE_REVIEW_BLOCK_MARKER")
    token = os.environ.get("AGENTFORGE_RECOVERY_IDENTITY_TOKEN")
    roles = None
    if marker_value is not None:
        if token is None:
            raise RuntimeError("blocking recovery Worker requires an identity token")
        roles = BlockingReviewer(
            DeterministicMockModel(
                storage, max_draft_bytes=storage.max_total_ticket_bytes
            ),
            Path(marker_value),
            token,
        )
    worker = await create_worker(settings, storage=storage, roles=roles)
    await worker.run()


def main() -> None:
    asyncio.run(run(Settings.from_env()), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    main()
