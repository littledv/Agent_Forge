from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

import pytest


PORTFOLIO_NODE_IDS: tuple[str, ...] = (
    "tests/integration/test_failure_portfolio.py::test_twenty_step_recovery_portfolio",
    "tests/integration/test_workflow.py::test_rejection_or_expiry_denies_delivery[rejected]",
    "tests/integration/test_workflow.py::test_retry_exhaustion_projects_uncertain_delivery",
    "tests/integration/test_multi_agent_workflow.py::test_two_revisions_are_allowed_and_third_rejection_exhausts_budget",
    "tests/integration/test_inbox_api.py::test_concurrent_receiver_attempts_commit_one_delivery",
)


def build_pytest_args(*, collect_only: bool = False) -> list[str]:
    args = [*PORTFOLIO_NODE_IDS, "-q"]
    if collect_only:
        args.append("--collect-only")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AgentForge failure portfolio.")
    parser.add_argument("--collect-only", action="store_true")
    options = parser.parse_args(argv)
    repository_root = str(Path(__file__).resolve().parents[2])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)
    return pytest.main(build_pytest_args(collect_only=options.collect_only))


if __name__ == "__main__":
    raise SystemExit(main())
