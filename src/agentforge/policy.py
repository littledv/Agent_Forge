"""Pure trusted policy for exact report-delivery authorization."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid5

from agentforge.domain import (
    ArtifactRef,
    DeliverReportAction,
    DeliveryAuthorization,
)


class UnauthorizedDeliveryAction(ValueError):
    """The proposed delivery does not match the trusted published artifact."""


def authorize_delivery(
    run_id: UUID,
    action: DeliverReportAction,
    artifact: ArtifactRef,
) -> DeliveryAuthorization:
    """Bind one trusted delivery action to stable approval and operation IDs."""

    if action.artifact != artifact:
        raise UnauthorizedDeliveryAction(
            "delivery action does not match the trusted artifact"
        )
    payload = action.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    action_hash = hashlib.sha256(encoded).hexdigest()
    return DeliveryAuthorization(
        approval_id=uuid5(run_id, f"approval:{action_hash}"),
        operation_id=uuid5(run_id, f"delivery:{action_hash}"),
        action_hash=action_hash,
        action=action,
    )
