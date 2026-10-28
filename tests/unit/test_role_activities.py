from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from temporalio.exceptions import ApplicationError

from agentforge.mock_model import DeterministicMockModel
from agentforge.model_profiles import MOCK_MODEL_PROFILE
from agentforge.multi_agent import ReviewDecision
from agentforge.role_activities import (
    CheckCancellationInput,
    ResearchActivityInput,
    ReviewReportActivityInput,
    RoleActivities,
    WriteReportActivityInput,
)
from agentforge.storage import ArtifactStorage


RUN_ID = UUID("00000000-0000-0000-0000-000000000006")


@pytest.fixture(autouse=True)
def stub_activity_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentforge.role_activities.activity.heartbeat", lambda: None)


def setup_runtime(tmp_path: Path) -> tuple[RoleActivities, ArtifactStorage]:
    tickets = tmp_path / "tickets"
    tickets.mkdir()
    (tickets / "a.txt").write_text("PRIVATE-A export is slow", encoding="utf-8")
    (tickets / "b.txt").write_text("PRIVATE-B reset is unclear", encoding="utf-8")
    storage = ArtifactStorage(
        ticket_root=tickets,
        artifact_root=tmp_path / "artifacts",
        max_ticket_bytes=65_536,
        max_total_ticket_bytes=262_144,
    )
    model = DeterministicMockModel(storage, max_draft_bytes=262_144)
    activities = RoleActivities(None, storage, model, MOCK_MODEL_PROFILE)  # type: ignore[arg-type]
    activities._record = AsyncMock()  # type: ignore[method-assign]
    return activities, storage


@pytest.mark.asyncio
async def test_research_activity_reads_only_assigned_snapshot_paths_and_writes_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activities, storage = setup_runtime(tmp_path)
    monkeypatch.setattr(
        "agentforge.role_activities.activity.info", lambda: SimpleNamespace(attempt=1)
    )
    snapshot = storage.snapshot_tickets(RUN_ID, ("a.txt", "b.txt"))

    result = await activities.research_role(
        ResearchActivityInput(
            run_id=RUN_ID,
            goal="Summarize tickets",
            ticket_snapshot=snapshot,
            assigned_paths=("b.txt",),
            shard_index=0,
            shard_count=1,
            step_ordinal=1,
            model_profile=MOCK_MODEL_PROFILE,
        )
    )

    body = storage.read_research_note(RUN_ID, result).decode()
    assert "b.txt" in body and "PRIVATE-B" in body
    assert "a.txt" not in body and "PRIVATE-A" not in body
    assert set(result.model_dump()) == {"path", "sha256", "size_bytes"}


@pytest.mark.asyncio
async def test_writer_and_reviewer_load_verified_refs_without_returning_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activities, storage = setup_runtime(tmp_path)
    monkeypatch.setattr(
        "agentforge.role_activities.activity.info", lambda: SimpleNamespace(attempt=2)
    )
    snapshot = storage.snapshot_tickets(RUN_ID, ("a.txt",))
    note = await activities.research_role(
        ResearchActivityInput(
            run_id=RUN_ID,
            goal="Summarize tickets",
            ticket_snapshot=snapshot,
            assigned_paths=("a.txt",),
            shard_index=0,
            shard_count=1,
            step_ordinal=1,
            model_profile=MOCK_MODEL_PROFILE,
        )
    )
    draft = await activities.write_report_role(
        WriteReportActivityInput(
            run_id=RUN_ID,
            goal="Summarize tickets",
            research_notes=(note,),
            revision=0,
            step_ordinal=5,
            model_profile=MOCK_MODEL_PROFILE,
        )
    )
    review = await activities.review_report_role(
        ReviewReportActivityInput(
            run_id=RUN_ID,
            goal="Summarize tickets",
            research_notes=(note,),
            draft=draft,
            review_round=0,
            step_ordinal=6,
            model_profile=MOCK_MODEL_PROFILE,
        )
    )

    assert set(draft.model_dump()) == {"path", "sha256", "size_bytes"}
    assert review.decision is ReviewDecision.ACCEPT
    assert review.feedback is None
    serialized = str(review.model_dump())
    assert "PRIVATE" not in serialized and "markdown" not in serialized


@pytest.mark.asyncio
async def test_role_activity_names_and_ordinals_are_exact_and_attempt_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activities, storage = setup_runtime(tmp_path)
    monkeypatch.setattr(
        "agentforge.role_activities.activity.info", lambda: SimpleNamespace(attempt=3)
    )
    snapshot = storage.snapshot_tickets(RUN_ID, ("a.txt",))
    await activities.research_role(
        ResearchActivityInput(
            run_id=RUN_ID,
            goal="Summarize tickets",
            ticket_snapshot=snapshot,
            assigned_paths=("a.txt",),
            shard_index=0,
            shard_count=1,
            step_ordinal=1,
            model_profile=MOCK_MODEL_PROFILE,
        )
    )
    calls = activities._record.await_args_list  # type: ignore[attr-defined]
    assert [call.kwargs["name"] for call in calls] == [
        "research_shard_1",
        "research_shard_1",
    ]
    assert all(call.kwargs["ordinal"] == 1 for call in calls)


@pytest.mark.asyncio
async def test_role_failures_are_classified_and_sanitized_like_version_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activities, storage = setup_runtime(tmp_path)
    monkeypatch.setattr(
        "agentforge.role_activities.activity.info", lambda: SimpleNamespace(attempt=1)
    )
    snapshot = storage.snapshot_tickets(RUN_ID, ("a.txt",))
    bad = snapshot.model_copy(update={"sha256": "b" * 64})
    with pytest.raises(ApplicationError) as caught:
        await activities.research_role(
            ResearchActivityInput(
                run_id=RUN_ID,
                goal="Summarize tickets",
                ticket_snapshot=bad,
                assigned_paths=("a.txt",),
                shard_index=0,
                shard_count=1,
                step_ordinal=1,
                model_profile=MOCK_MODEL_PROFILE,
            )
        )
    assert caught.value.non_retryable
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.asyncio
async def test_check_cancellation_reads_postgresql_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exists = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "agentforge.role_activities.CancellationRepository.exists", exists
    )

    class Context:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args: object):
            return None

    activities = RoleActivities(lambda: Context(), None, None, MOCK_MODEL_PROFILE)  # type: ignore[arg-type]
    assert await activities.check_cancellation(CheckCancellationInput(run_id=RUN_ID))
    exists.assert_awaited_once()
