from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from agentforge.model_profiles import build_ollama_profile
from agentforge.multi_agent import ReviewDecision
from agentforge.ollama_model import ModelOutputInvalid, ModelRequestRejected
from agentforge.ollama_roles import OllamaRoleProvider
from agentforge.role_runtime import (
    LoadedResearchNote,
    ReportBody,
    ResearchFinding,
    ResearchNoteBody,
    ResearchRoleRequest,
    ReviewBody,
    ReviewCategory,
    ReviewFeedbackBody,
    ReviewFeedbackItem,
    ReviewerRoleRequest,
    VerifiedTicket,
    WriterRoleRequest,
)


RUN_ID = UUID("00000000-0000-0000-0000-000000000005")
PRIVATE = "PRIVATE-TICKET-SENTINEL"


def profile():
    return build_ollama_profile(
        model_name="qwen3:4b-instruct-2507-q4_K_M",
        model_digest="a" * 64,
        context_tokens=8_192,
        max_output_tokens=2_048,
        max_prompt_chars=24_000,
        max_report_chars=16_384,
        max_repairs=2,
    )


class UnusedUsage:
    async def request_started(self, *, estimated_input_tokens: int) -> None:
        return None

    async def response_completed(self, **kwargs: object) -> None:
        return None


class RecordingStructuredClient:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    async def generate_structured(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def research_request() -> ResearchRoleRequest:
    return ResearchRoleRequest(
        run_id=RUN_ID,
        goal="Summarize assigned tickets",
        tickets=(VerifiedTicket(path="a.txt", content=PRIVATE),),
        shard_index=0,
        shard_count=1,
        graph_version=2,
        model_profile=profile(),
    )


def note(path: str = "a.txt") -> ResearchNoteBody:
    return ResearchNoteBody(
        findings=(
            ResearchFinding(
                path=path,
                observation="Observed issue",
                evidence_excerpt="Evidence excerpt",
            ),
        )
    )


def writer_request(**overrides: object) -> WriterRoleRequest:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "goal": "Summarize assigned tickets",
        "notes": (LoadedResearchNote(shard_index=0, body=note()),),
        "revision": 0,
        "graph_version": 2,
        "model_profile": profile(),
    }
    values.update(overrides)
    return WriterRoleRequest.model_validate(values)


def reviewer_request() -> ReviewerRoleRequest:
    return ReviewerRoleRequest(
        run_id=RUN_ID,
        goal="Summarize assigned tickets",
        notes=writer_request().notes,
        report=ReportBody(
            markdown="# Report\nSummarize assigned tickets\na.txt"
        ),
        review_round=0,
        graph_version=2,
        model_profile=profile(),
    )


@pytest.mark.asyncio
async def test_research_prompt_contains_only_goal_assigned_tickets_and_strict_schema() -> None:
    client = RecordingStructuredClient(note())
    provider = OllamaRoleProvider(profile(), client)  # type: ignore[arg-type]

    result = await provider.research(research_request(), UnusedUsage())

    assert result == note()
    call = client.calls[0]
    document = call["user_document"]
    assert isinstance(document, dict)
    assert document["goal"] == "Summarize assigned tickets"
    assert document["assigned_tickets"] == [{"path": "a.txt", "content": PRIVATE}]
    assert document["output_skeleton"] == {
        "findings": [
            {
                "path": "a.txt",
                "observation": "one concise observation",
                "evidence_excerpt": "one concise excerpt",
            }
        ]
    }
    assert "outside" not in str(document).lower()
    output_type = call["output_type"]
    assert isinstance(output_type, type)
    schema = output_type.model_json_schema()
    finding_schema = schema["$defs"]["AssignedResearchFinding"]
    assert finding_schema["properties"]["path"]["const"] == "a.txt"
    assert schema["properties"]["findings"]["minItems"] == 1
    assert schema["properties"]["findings"]["maxItems"] == 1


@pytest.mark.asyncio
async def test_writer_prompt_uses_ordered_notes_and_bounded_revision_feedback() -> None:
    feedback = ReviewFeedbackBody(
        items=(ReviewFeedbackItem(category=ReviewCategory.FORMAT, text="Add heading"),)
    )
    request = writer_request(
        revision=1,
        previous_report=ReportBody(markdown="# Old"),
        feedback=feedback,
    )
    report = ReportBody(markdown="# Report\nSummarize assigned tickets\na.txt")
    client = RecordingStructuredClient(report)

    await OllamaRoleProvider(profile(), client).write(  # type: ignore[arg-type]
        request, UnusedUsage()
    )

    document = client.calls[0]["user_document"]
    assert isinstance(document, dict)
    assert document["research_notes"][0]["shard_index"] == 0  # type: ignore[index]
    assert document["feedback"]["items"][0]["text"] == "Add heading"  # type: ignore[index]
    assert document["revision"] == 1


@pytest.mark.asyncio
async def test_reviewer_prompt_cannot_authorize_delivery_or_add_tasks() -> None:
    report = ReportBody(markdown="# Report\nSummarize assigned tickets\na.txt")
    request = ReviewerRoleRequest(
        run_id=RUN_ID,
        goal="Summarize assigned tickets",
        notes=writer_request().notes,
        report=report,
        review_round=0,
        graph_version=2,
        model_profile=profile(),
    )
    client = RecordingStructuredClient(ReviewBody(decision=ReviewDecision.ACCEPT))

    await OllamaRoleProvider(profile(), client).review(  # type: ignore[arg-type]
        request, UnusedUsage()
    )

    serialized = str(client.calls[0]).lower()
    assert "cannot authorize delivery" in serialized
    assert "cannot add tasks" in serialized
    assert "operation_id" not in serialized


@pytest.mark.asyncio
async def test_role_validation_rejects_missing_duplicate_and_outside_paths() -> None:
    client = RecordingStructuredClient(note("outside.txt"), note("outside.txt"), note("outside.txt"))
    provider = OllamaRoleProvider(profile(), client)  # type: ignore[arg-type]

    with pytest.raises(ModelOutputInvalid, match="repair_exhausted"):
        await provider.research(research_request(), UnusedUsage())
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_each_role_allows_two_sanitized_repairs_then_stops() -> None:
    errors = (ModelOutputInvalid("invalid_schema"),) * 3
    requests_and_methods = [
        (research_request(), "research"),
        (writer_request(), "write"),
    ]
    for request, method in requests_and_methods:
        client = RecordingStructuredClient(*errors)
        provider = OllamaRoleProvider(profile(), client)  # type: ignore[arg-type]
        with pytest.raises(ModelOutputInvalid, match="repair_exhausted"):
            await getattr(provider, method)(request, UnusedUsage())
        assert len(client.calls) == 3
        assert PRIVATE not in str(client.calls[1].get("repair_category", ""))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "role_request"),
    (
        ("research", research_request()),
        ("write", writer_request()),
        ("review", reviewer_request()),
    ),
)
async def test_trusted_request_rejection_is_not_repaired(
    method: str,
    role_request: ResearchRoleRequest | WriterRoleRequest | ReviewerRoleRequest,
) -> None:
    client = RecordingStructuredClient(
        ModelRequestRejected("trusted request rejected"),
        ModelRequestRejected("must not retry"),
        ModelRequestRejected("must not retry"),
    )
    provider = OllamaRoleProvider(profile(), client)  # type: ignore[arg-type]

    with pytest.raises(ModelRequestRejected, match="trusted request rejected"):
        await getattr(provider, method)(role_request, UnusedUsage())

    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_missing_usage_stays_uncertain_and_does_not_expand_request_count() -> None:
    client = RecordingStructuredClient(note())
    await OllamaRoleProvider(profile(), client).research(  # type: ignore[arg-type]
        research_request(), UnusedUsage()
    )
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_role_errors_never_include_ticket_note_draft_feedback_or_raw_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = RecordingStructuredClient(*(ModelOutputInvalid(PRIVATE) for _ in range(3)))
    provider = OllamaRoleProvider(profile(), client)  # type: ignore[arg-type]
    with pytest.raises(ModelOutputInvalid) as caught:
        await provider.research(research_request(), UnusedUsage())
    assert PRIVATE not in str(caught.value)
    assert PRIVATE not in caplog.text
