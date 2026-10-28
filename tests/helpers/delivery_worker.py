"""Native Worker subprocess for the post-receiver-commit recovery proof."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from agentforge.config import Settings
from agentforge.domain import DeliveryReceipt, DeliveryRequest
from agentforge.inbox_client import InboxClient
from agentforge.worker import create_worker


class BlockingCommittedInboxClient:
    def __init__(self, delegate: InboxClient, marker_path: Path) -> None:
        self._delegate = delegate
        self._marker_path = marker_path

    def deliver(self, request: DeliveryRequest) -> DeliveryReceipt:
        receipt = self._delegate.deliver(request)
        temporary = self._marker_path.with_suffix(".tmp")
        temporary.write_text("committed\n", encoding="utf-8")
        os.replace(temporary, self._marker_path)
        threading.Event().wait()
        raise RuntimeError("unreachable")


async def run(settings: Settings) -> None:
    marker_value = os.environ.get("AGENTFORGE_DELIVERY_COMMIT_MARKER")
    if marker_value is None:
        worker = await create_worker(settings)
    else:
        inbox_client = BlockingCommittedInboxClient(
            InboxClient(settings.inbox_base_url), Path(marker_value)
        )
        worker = await create_worker(settings, inbox_client=inbox_client)
    await worker.run()


def main() -> None:
    asyncio.run(
        run(Settings.from_env()), loop_factory=asyncio.SelectorEventLoop
    )


if __name__ == "__main__":
    main()
