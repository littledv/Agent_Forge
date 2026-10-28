from uuid import UUID

import pytest
from pydantic import ValidationError

from agentforge.model_profiles import MOCK_MODEL_PROFILE
from agentforge.multi_agent import MultiAgentWorkflowInput, SUPPORT_REPORT_GRAPH_V2
from agentforge.role_runtime import (
    LoadedResearchNote,
    ResearchFinding,
    ResearchNoteBody,
    ResearchRoleRequest,
    VerifiedTicket,
    WriterRoleRequest,
)


RUN_ID = UUID("00000000-0000-0000-0000-000000000004")


def finding(path: str) -> ResearchFinding:
    return ResearchFinding(
        path=path,
        observation=f"Observed issue in {path}",
        evidence_excerpt=f"Evidence from {path}",
    )


def test_activity_local_role_requests_are_strict_and_never_reference_paths_outside_inputs() -> None:
    request = ResearchRoleRequest(
        run_id=RUN_ID,
        goal="Summarize tickets",
        tickets=(VerifiedTicket(path="a.txt", content="Private ticket body"),),
        shard_index=0,
        shard_count=1,
        graph_version=2,
        model_profile=MOCK_MODEL_PROFILE,
    )
    assert request.tickets[0].content == "Private ticket body"
    with pytest.raises(ValidationError):
        ResearchRoleRequest.model_validate(
            {**request.model_dump(), "outside_paths": ["outside.txt"]}
        )
    with pytest.raises(ValidationError):
        MultiAgentWorkflowInput(
            run_id=RUN_ID,
            goal="Summarize tickets",
            ticket_files=("a.txt",),
            model_profile=MOCK_MODEL_PROFILE,
            limits=SUPPORT_REPORT_GRAPH_V2,
            ticket_bodies={"a.txt": "Private ticket body"},
        )


def test_research_note_requires_exact_unique_assigned_path_coverage() -> None:
    exact = ResearchNoteBody(findings=(finding("a.txt"), finding("b.txt")))
    assert exact.require_exact_paths(("a.txt", "b.txt")) is exact

    with pytest.raises(ValidationError, match="unique"):
        ResearchNoteBody(findings=(finding("a.txt"), finding("a.txt")))
    with pytest.raises(ValueError, match="exactly cover"):
        ResearchNoteBody(findings=(finding("a.txt"),)).require_exact_paths(
            ("a.txt", "b.txt")
        )
    with pytest.raises(ValueError, match="exactly cover"):
        ResearchNoteBody(
            findings=(finding("a.txt"), finding("outside.txt"))
        ).require_exact_paths(("a.txt", "b.txt"))
    with pytest.raises(ValidationError):
        ResearchFinding(
            path="a.txt",
            observation="x" * 513,
            evidence_excerpt="evidence",
        )


def test_writer_request_requires_ordered_notes_and_revision_state() -> None:
    first = LoadedResearchNote(
        shard_index=0, body=ResearchNoteBody(findings=(finding("a.txt"),))
    )
    second = LoadedResearchNote(
        shard_index=1, body=ResearchNoteBody(findings=(finding("b.txt"),))
    )
    request = WriterRoleRequest(
        run_id=RUN_ID,
        goal="Summarize tickets",
        notes=(first, second),
        revision=0,
        graph_version=2,
        model_profile=MOCK_MODEL_PROFILE,
    )
    assert request.ticket_paths == ("a.txt", "b.txt")
    with pytest.raises(ValidationError, match="shard order"):
        WriterRoleRequest.model_validate(
            {**request.model_dump(), "notes": (second, first)}
        )
