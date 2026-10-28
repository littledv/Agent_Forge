"""Run the complete AgentForge lifecycle through the public Python SDK."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from agentforge import AgentForgeClient, ApprovalDecision, RunState


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Submit local ticket files, review the delivery approval, and wait "
            "for the AgentForge run to finish."
        )
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--submission-key", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument(
        "ticket_files",
        nargs="+",
        metavar="TICKET_FILE",
        help="relative path beneath the configured ticket root",
    )
    return parser.parse_args(argv)


def _read_decision() -> ApprovalDecision:
    while True:
        value = input("Decision (approve/reject): ").strip().lower()
        if value == "approve":
            return ApprovalDecision.APPROVE
        if value == "reject":
            return ApprovalDecision.REJECT
        print("Enter exactly 'approve' or 'reject'.")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    with AgentForgeClient(args.base_url) as client:
        submitted = client.submit_run(
            submission_key=args.submission_key,
            goal=args.goal,
            ticket_files=args.ticket_files,
        )
        print(f"run_id={submitted.run_id}")

        waiting = client.wait_for_run(
            submitted.run_id,
            states={RunState.WAITING_APPROVAL},
        )
        print(f"state={waiting.state.value}")

        approval = client.get_approval(submitted.run_id)
        print(f"approval_id={approval.approval_id}")
        print(f"action_hash={approval.action_hash}")
        client.decide_approval(
            approval.approval_id,
            action_hash=approval.action_hash,
            decision=_read_decision(),
        )

        completed = client.wait_for_run(submitted.run_id)
        print(f"state={completed.state.value}")
        if completed.state == RunState.SUCCEEDED:
            artifact = client.get_artifact(submitted.run_id).artifact
            print(f"artifact_path={artifact.path}")
            print(f"artifact_sha256={artifact.sha256}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
