from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from agentforge.workflow import SupportReportWorkflow


@pytest.mark.integration
@pytest.mark.parametrize(
    "fixture_name",
    ("support_report_v1_approved.json", "support_report_v1_denied.json"),
)
async def test_saved_v1_histories_replay_with_support_report_workflow(
    fixture_name: str,
) -> None:
    fixture = Path("tests/fixtures/temporal") / fixture_name
    history = WorkflowHistory.from_json(fixture.stem, fixture.read_text(encoding="utf-8"))

    await Replayer(
        workflows=[SupportReportWorkflow],
        data_converter=pydantic_data_converter,
    ).replay_workflow(history)
