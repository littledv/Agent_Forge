from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from agentforge.domain import (
    ArtifactRef,
    DeliverReportAction,
    DeliveryReceipt,
    FinishAction,
    ModelDecisionInput,
    PublishReportAction,
    ReadTicketsAction,
    WorkflowInput,
)
from agentforge.model_profiles import MOCK_MODEL_PROFILE
from agentforge.mock_model import DeterministicMockModel, InvalidModelState
from agentforge.role_runtime import (
    LoadedResearchNote,
    ReportBody,
    ResearchFinding,
    ResearchNoteBody,
    ResearchRoleRequest,
    ReviewCategory,
    ReviewFeedbackBody,
    ReviewFeedbackItem,
    ReviewerRoleRequest,
    VerifiedTicket,
    WriterRoleRequest,
)
from agentforge.storage import ArtifactStorage


RUN_ID = UUID("00000000-0000-0000-0000-000000000002")


def model_and_storage(tmp_path: Path) -> tuple[DeterministicMockModel, ArtifactStorage]:
    tickets = tmp_path / "tickets"
    artifacts = tmp_path / "artifacts"
    tickets.mkdir()
    (tickets / "b.md").write_text("Password reset is unclear.\n", encoding="utf-8")
    (tickets / "a.txt").write_text("Export is slow.\n", encoding="utf-8")
    store = ArtifactStorage(
        ticket_root=tickets,
        artifact_root=artifacts,
        max_ticket_bytes=65_536,
        max_total_ticket_bytes=262_144,
    )
    return DeterministicMockModel(store, max_draft_bytes=131_072), store


def decision_input(**overrides: object) -> ModelDecisionInput:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "goal": "Find recurring support themes",
        "ticket_files": ("b.md", "a.txt"),
        "decision_number": 1,
    }
    values.update(overrides)
    return ModelDecisionInput(**values)


class UnusedUsageReporter:
    async def request_started(self, *, estimated_input_tokens: int) -> None:
        raise AssertionError("mock model must not report provider usage")

    async def response_completed(
        self,
        *,
        estimated_input_tokens: int,
        input_tokens: int | None,
        output_tokens: int | None,
        duration_ms: int | None,
    ) -> None:
        raise AssertionError("mock model must not report provider usage")


@pytest.mark.asyncio
async def test_async_empty_state_requests_the_approved_ticket_read(
    tmp_path: Path,
) -> None:
    model, _ = model_and_storage(tmp_path)

    action = await model.decide(decision_input(), UnusedUsageReporter())

    assert action == ReadTicketsAction(
        type="read_tickets", ticket_files=("b.md", "a.txt")
    )


@pytest.mark.asyncio
async def test_async_snapshot_produces_the_same_bounded_report_every_time(
    tmp_path: Path,
) -> None:
    model, store = model_and_storage(tmp_path)
    snapshot = store.snapshot_tickets(RUN_ID, ("b.md", "a.txt"))
    state = decision_input(ticket_snapshot=snapshot, decision_number=2)

    first = await model.decide(state, UnusedUsageReporter())
    first_bytes = (store.artifact_root / first.draft.path).read_bytes()
    second = await model.decide(state, UnusedUsageReporter())
    second_bytes = (store.artifact_root / second.draft.path).read_bytes()

    assert isinstance(first, PublishReportAction)
    assert second == first
    assert first_bytes == second_bytes
    assert len(first_bytes) <= 131_072
    text = first_bytes.decode("utf-8")
    assert text.startswith("# Support Report\n")
    assert "Find recurring support themes" in text
    assert "Ticket count: 2" in text
    assert text.index("## a.txt") < text.index("## b.md")
    assert text.endswith("Reviewed 2 support tickets in lexical order.\n")


@pytest.mark.asyncio
async def test_async_final_artifact_requests_delivery_then_finish(
    tmp_path: Path,
) -> None:
    model, store = model_and_storage(tmp_path)
    snapshot = store.snapshot_tickets(RUN_ID, ("a.txt",))
    publish = await model.decide(
        decision_input(ticket_files=("a.txt",), ticket_snapshot=snapshot, decision_number=2),
        UnusedUsageReporter(),
    )
    artifact = store.publish_report(RUN_ID, publish.draft)

    delivery_action = await model.decide(
        decision_input(
            ticket_files=("a.txt",),
            ticket_snapshot=snapshot,
            draft=publish.draft,
            artifact=artifact,
            decision_number=3,
        ),
        UnusedUsageReporter(),
    )

    assert delivery_action == DeliverReportAction(
        type="deliver_report", target="simulated-inbox", artifact=artifact
    )

    receipt = DeliveryReceipt(
        delivery_id=uuid4(),
        operation_id=uuid4(),
        artifact_sha256=artifact.sha256,
        committed_at=datetime.now(UTC),
    )
    finish = await model.decide(
        decision_input(
            ticket_files=("a.txt",),
            ticket_snapshot=snapshot,
            draft=publish.draft,
            artifact=artifact,
            delivery=receipt,
            decision_number=4,
        ),
        UnusedUsageReporter(),
    )
    assert finish == FinishAction(type="finish", artifact=artifact)


@pytest.mark.parametrize(
    "overrides",
    [
        {"draft": {"path": "drafts/x.md", "sha256": "a" * 64, "size_bytes": 1}},
        {
            "artifact": ArtifactRef(
                path="reports/x.md",
                sha256="a" * 64,
                size_bytes=1,
                media_type="text/markdown",
            )
        },
    ],
)
@pytest.mark.asyncio
async def test_async_malformed_state_fails_instead_of_guessing(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    model, _ = model_and_storage(tmp_path)

    with pytest.raises(InvalidModelState):
        await model.decide(decision_input(**overrides), UnusedUsageReporter())


def test_legacy_workflow_and_decision_inputs_default_to_mock_profile() -> None:
    workflow_input = WorkflowInput.model_validate(
        {
                "run_id": RUN_ID,
            "goal": "Find recurring support themes",
            "ticket_files": ["a.txt"],
            "max_decisions": 4,
        }
    )
    decision = ModelDecisionInput.model_validate(
        {
                "run_id": RUN_ID,
            "goal": "Find recurring support themes",
            "ticket_files": ["a.txt"],
            "decision_number": 1,
        }
    )

    assert workflow_input.model_profile == MOCK_MODEL_PROFILE
    assert decision.model_profile == MOCK_MODEL_PROFILE


def research_request() -> ResearchRoleRequest:
    return ResearchRoleRequest(
        run_id=RUN_ID,
        goal="Find recurring support themes",
        tickets=(
            VerifiedTicket(path="a.txt", content="Export is slow.\nMore detail."),
            VerifiedTicket(path="b.md", content="Password reset is unclear.\n"),
        ),
        shard_index=0,
        shard_count=1,
        graph_version=2,
        model_profile=MOCK_MODEL_PROFILE,
    )


def loaded_note() -> LoadedResearchNote:
    return LoadedResearchNote(
        shard_index=0,
        body=ResearchNoteBody(
            findings=(
                ResearchFinding(
                    path="a.txt",
                    observation="Export is slow.",
                    evidence_excerpt="Export is slow. More detail.",
                ),
                ResearchFinding(
                    path="b.md",
                    observation="Password reset is unclear.",
                    evidence_excerpt="Password reset is unclear.",
                ),
            )
        ),
    )


def writer_request(**overrides: object) -> WriterRoleRequest:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "goal": "Find recurring support themes",
        "notes": (loaded_note(),),
        "revision": 0,
        "graph_version": 2,
        "model_profile": MOCK_MODEL_PROFILE,
    }
    values.update(overrides)
    return WriterRoleRequest.model_validate(values)


@pytest.mark.asyncio
async def test_mock_research_emits_one_stable_finding_per_assigned_ticket(
    tmp_path: Path,
) -> None:
    model, _ = model_and_storage(tmp_path)

    first = await model.research(research_request(), UnusedUsageReporter())
    second = await model.research(research_request(), UnusedUsageReporter())

    assert first == second
    assert tuple(item.path for item in first.findings) == ("a.txt", "b.md")
    assert first.findings[0].observation == "Export is slow."
    assert first.require_exact_paths(("a.txt", "b.md")) is first


@pytest.mark.asyncio
async def test_mock_writer_is_stable_and_covers_goal_and_every_ticket_path(
    tmp_path: Path,
) -> None:
    model, _ = model_and_storage(tmp_path)
    request = writer_request()

    first = await model.write(request, UnusedUsageReporter())
    second = await model.write(request, UnusedUsageReporter())

    assert first == second
    assert request.goal in first.markdown
    assert all(path in first.markdown for path in request.ticket_paths)
    assert first.markdown.startswith("# Support Report\n")


@pytest.mark.asyncio
async def test_mock_writer_applies_bounded_feedback_on_revision(tmp_path: Path) -> None:
    model, _ = model_and_storage(tmp_path)
    previous = ReportBody(markdown="# Old report\n")
    feedback = ReviewFeedbackBody(
        items=(
            ReviewFeedbackItem(
                category=ReviewCategory.UNCLEAR_SUMMARY,
                text="Clarify the summary",
            ),
        )
    )

    revised = await model.write(
        writer_request(revision=1, previous_report=previous, feedback=feedback),
        UnusedUsageReporter(),
    )

    assert "Revision 1" in revised.markdown
    assert "UNCLEAR_SUMMARY: Clarify the summary" in revised.markdown
    assert len(revised.markdown) <= MOCK_MODEL_PROFILE.max_report_chars or (
        MOCK_MODEL_PROFILE.max_report_chars == 0
    )


@pytest.mark.asyncio
async def test_mock_reviewer_accepts_valid_report_and_revises_invalid_report(
    tmp_path: Path,
) -> None:
    model, _ = model_and_storage(tmp_path)
    request = writer_request()
    report = await model.write(request, UnusedUsageReporter())
    reviewer = ReviewerRoleRequest(
        run_id=RUN_ID,
        goal=request.goal,
        notes=request.notes,
        report=report,
        review_round=0,
        graph_version=2,
        model_profile=MOCK_MODEL_PROFILE,
    )

    accepted = await model.review(reviewer, UnusedUsageReporter())
    revised = await model.review(
        reviewer.model_copy(update={"report": ReportBody(markdown="# Wrong\n")}),
        UnusedUsageReporter(),
    )

    assert accepted.decision.value == "ACCEPT"
    assert accepted.feedback is None
    assert revised.decision.value == "REVISE"
    assert revised.feedback is not None
    assert {item.category for item in revised.feedback.items} == {
        ReviewCategory.MISSING_GOAL,
        ReviewCategory.MISSING_TICKET,
    }


@pytest.mark.asyncio
async def test_mock_roles_never_record_provider_usage(tmp_path: Path) -> None:
    model, _ = model_and_storage(tmp_path)
    note = await model.research(research_request(), UnusedUsageReporter())
    loaded = LoadedResearchNote(shard_index=0, body=note)
    write_request = writer_request(notes=(loaded,))
    report = await model.write(write_request, UnusedUsageReporter())
    review_request = ReviewerRoleRequest(
        run_id=RUN_ID,
        goal=write_request.goal,
        notes=write_request.notes,
        report=report,
        review_round=0,
        graph_version=2,
        model_profile=MOCK_MODEL_PROFILE,
    )

    assert (await model.review(review_request, UnusedUsageReporter())).decision.value == (
        "ACCEPT"
    )
