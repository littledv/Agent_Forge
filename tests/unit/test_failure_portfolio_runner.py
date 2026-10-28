from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

import tests.helpers.run_failure_portfolio as runner


EXPECTED_NODE_IDS = (
    "tests/integration/test_failure_portfolio.py::test_twenty_step_recovery_portfolio",
    "tests/integration/test_workflow.py::test_rejection_or_expiry_denies_delivery[rejected]",
    "tests/integration/test_workflow.py::test_retry_exhaustion_projects_uncertain_delivery",
    "tests/integration/test_multi_agent_workflow.py::test_two_revisions_are_allowed_and_third_rejection_exhausts_budget",
    "tests/integration/test_inbox_api.py::test_concurrent_receiver_attempts_commit_one_delivery",
)


def test_portfolio_manifest_is_the_exact_fixed_failure_matrix() -> None:
    assert runner.PORTFOLIO_NODE_IDS == EXPECTED_NODE_IDS
    assert len(runner.PORTFOLIO_NODE_IDS) == 5
    assert not any("duplicate" in node for node in runner.PORTFOLIO_NODE_IDS)


@pytest.mark.parametrize(
    ("collect_only", "expected_suffix"),
    [(False, ["-q"]), (True, ["-q", "--collect-only"])],
)
def test_build_pytest_args_uses_only_fixed_nodes_and_requested_mode(
    collect_only: bool, expected_suffix: list[str]
) -> None:
    assert runner.build_pytest_args(collect_only=collect_only) == [
        *EXPECTED_NODE_IDS,
        *expected_suffix,
    ]


@pytest.mark.parametrize(
    ("argv", "expected_args"),
    [
        ([], [*EXPECTED_NODE_IDS, "-q"]),
        (["--collect-only"], [*EXPECTED_NODE_IDS, "-q", "--collect-only"]),
    ],
)
def test_main_returns_pytest_exit_code_unchanged(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], expected_args: list[str]
) -> None:
    received: list[list[str]] = []

    def fake_pytest_main(args: list[str]) -> int:
        received.append(args)
        return 7

    monkeypatch.setattr(runner.pytest, "main", fake_pytest_main)

    assert runner.main(argv) == 7
    assert received == [expected_args]


def test_unknown_arguments_fail_before_pytest_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fake_pytest_main(_args: list[str]) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(runner.pytest, "main", fake_pytest_main)

    with pytest.raises(SystemExit) as exc_info:
        runner.main(["--unexpected"])

    assert exc_info.value.code == 2
    assert called is False


def test_script_entrypoint_collects_manifest_from_repository_root() -> None:
    repository_root = Path(__file__).parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(repository_root / "tests" / "helpers" / "run_failure_portfolio.py"),
            "--collect-only",
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2_000:]
    assert "5 tests collected" in result.stdout
