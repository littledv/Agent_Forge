from uuid import UUID

import pytest
from pydantic import ValidationError

from agentforge.domain import ModelProfileSnapshot
from agentforge.multi_agent import (
    APPROVAL_ORDINAL,
    DELIVERY_ORDINAL,
    POST_DELIVERY_GATE_ORDINAL,
    PRE_DELIVERY_GATE_ORDINAL,
    PUBLICATION_ORDINAL,
    READ_TICKETS_ORDINAL,
    MultiAgentWorkflowInput,
    ReviewDecision,
    ReviewFeedbackRef,
    ReviewResult,
    SUPPORT_REPORT_GRAPH_V2,
    partition_ticket_paths,
    research_ordinal,
    reviewer_ordinal,
    structural_provider_request_limit,
    writer_ordinal,
)


SHA256 = "a" * 64
RUN_ID = UUID("00000000-0000-0000-0000-000000000001")


def ollama_profile() -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id="ollama-qwen3-4b-instruct-q4-v1",
        provider="ollama",
        model_name="qwen3:4b-instruct-2507-q4_K_M",
        model_digest="b" * 64,
        task_queue="agentforge-support-report-ollama-qwen3-4b-v1",
        context_tokens=8_192,
        max_output_tokens=2_048,
        max_prompt_chars=24_000,
        max_report_chars=16_384,
        max_repairs=2,
    )


def test_graph_v2_limits_are_exact_strict_and_frozen() -> None:
    assert SUPPORT_REPORT_GRAPH_V2.model_dump() == {
        "graph_version": 2,
        "max_ticket_files": 16,
        "max_research_tasks": 4,
        "max_concurrent_research": 2,
        "max_revisions": 2,
        "max_role_tasks": 10,
        "max_elapsed_seconds": 900,
        "max_research_note_chars": 4_096,
        "max_review_feedback_chars": 2_048,
    }
    with pytest.raises(ValidationError):
        SUPPORT_REPORT_GRAPH_V2.__class__.model_validate(
            {**SUPPORT_REPORT_GRAPH_V2.model_dump(), "max_ticket_files": "16"}
        )
    with pytest.raises(ValidationError):
        SUPPORT_REPORT_GRAPH_V2.max_ticket_files = 17


def test_partition_ticket_paths_is_sorted_round_robin_and_bounded() -> None:
    assert partition_ticket_paths(
        ("d", "b", "a", "c", "e"), SUPPORT_REPORT_GRAPH_V2
    ) == (("a", "e"), ("b",), ("c",), ("d",))
    assert partition_ticket_paths(("b", "a"), SUPPORT_REPORT_GRAPH_V2) == (
        ("a",),
        ("b",),
    )
    with pytest.raises(ValueError, match="at most 16"):
        partition_ticket_paths(
            tuple(f"ticket-{index}.txt" for index in range(17)),
            SUPPORT_REPORT_GRAPH_V2,
        )


def test_role_ordinals_are_stable_for_missing_and_out_of_order_shards() -> None:
    assert READ_TICKETS_ORDINAL == 0
    assert [research_ordinal(index) for index in (3, 0, 2, 1)] == [4, 1, 3, 2]
    assert [writer_ordinal(revision) for revision in range(3)] == [5, 7, 9]
    assert [reviewer_ordinal(review_round) for review_round in range(3)] == [6, 8, 10]
    assert PUBLICATION_ORDINAL == 11
    assert APPROVAL_ORDINAL == 12
    assert PRE_DELIVERY_GATE_ORDINAL == 13
    assert DELIVERY_ORDINAL == 14
    assert POST_DELIVERY_GATE_ORDINAL == 15
    with pytest.raises(ValueError):
        research_ordinal(4)
    with pytest.raises(ValueError):
        writer_ordinal(3)


def test_review_result_requires_feedback_only_for_revise() -> None:
    feedback = ReviewFeedbackRef(
        path="reviews/run-1/round-0/" + SHA256 + ".json",
        sha256=SHA256,
        size_bytes=100,
    )
    assert ReviewResult(decision=ReviewDecision.ACCEPT).feedback is None
    assert ReviewResult(decision=ReviewDecision.REVISE, feedback=feedback).feedback == feedback
    with pytest.raises(ValidationError):
        ReviewResult(decision=ReviewDecision.REVISE)
    with pytest.raises(ValidationError):
        ReviewResult(decision=ReviewDecision.ACCEPT, feedback=feedback)


def test_structural_request_limit_is_ninety_for_approved_profile() -> None:
    profile = ollama_profile()
    assert structural_provider_request_limit(
        SUPPORT_REPORT_GRAPH_V2,
        activity_attempts=3,
        max_repairs=profile.max_repairs,
    ) == 90
    with pytest.raises(ValueError):
        structural_provider_request_limit(
            SUPPORT_REPORT_GRAPH_V2, activity_attempts=0, max_repairs=2
        )


def test_multi_agent_workflow_input_rejects_seventeenth_ticket() -> None:
    valid = {
        "run_id": RUN_ID,
        "goal": "Summarize the support tickets",
        "ticket_files": tuple(f"ticket-{index}.txt" for index in range(16)),
        "model_profile": ollama_profile(),
        "limits": SUPPORT_REPORT_GRAPH_V2,
    }
    request = MultiAgentWorkflowInput.model_validate(valid)
    assert len(request.ticket_files) == 16
    with pytest.raises(ValidationError, match="at most 16"):
        MultiAgentWorkflowInput.model_validate(
            {**valid, "ticket_files": (*valid["ticket_files"], "ticket-16.txt")}
        )
    with pytest.raises(ValidationError):
        MultiAgentWorkflowInput.model_validate(
            {**valid, "ticket_files": ("../outside.txt",)}
        )
