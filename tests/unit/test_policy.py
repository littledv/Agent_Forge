from __future__ import annotations

import importlib
from uuid import UUID

import pytest

from agentforge.domain import ArtifactRef


RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
SHA256 = "a" * 64


def _policy_module():
    try:
        return importlib.import_module("agentforge.policy")
    except ModuleNotFoundError:
        pytest.fail("agentforge.policy does not exist")


def _domain_module():
    import agentforge.domain as domain

    assert hasattr(domain, "DeliverReportAction")
    return domain


def artifact(**overrides: object) -> ArtifactRef:
    values: dict[str, object] = {
        "path": "reports/run-1/support-report.md",
        "sha256": SHA256,
        "size_bytes": 20,
        "media_type": "text/markdown",
    }
    values.update(overrides)
    return ArtifactRef(**values)


def test_authorization_is_stable_for_the_same_exact_action() -> None:
    policy = _policy_module()
    domain = _domain_module()
    trusted = artifact()
    first_action = domain.DeliverReportAction.model_validate(
        {
            "artifact": trusted.model_dump(mode="json"),
            "target": "simulated-inbox",
            "type": "deliver_report",
        }
    )
    reordered_action = domain.DeliverReportAction.model_validate(
        {
            "type": "deliver_report",
            "target": "simulated-inbox",
            "artifact": {
                "media_type": "text/markdown",
                "size_bytes": 20,
                "sha256": SHA256,
                "path": "reports/run-1/support-report.md",
            },
        }
    )

    first = policy.authorize_delivery(RUN_ID, first_action, trusted)
    reordered = policy.authorize_delivery(RUN_ID, reordered_action, trusted)

    assert first == reordered
    assert first.action_hash == "8878cea38ad7a2570b8de88124b77890a0d7d8feb0c0d5bb9bb30572de9e6df8"
    assert first.approval_id == UUID("8f911514-2839-5622-b3cc-df154fbef74e")
    assert first.operation_id == UUID("562dc011-61cc-54b3-9363-d51a3a1a2f40")


def test_authorization_rejects_changed_artifact_arguments() -> None:
    policy = _policy_module()
    domain = _domain_module()
    trusted = artifact()
    changed = artifact(size_bytes=21)
    action = domain.DeliverReportAction(
        type="deliver_report", target="simulated-inbox", artifact=changed
    )

    with pytest.raises(policy.UnauthorizedDeliveryAction):
        policy.authorize_delivery(RUN_ID, action, trusted)
