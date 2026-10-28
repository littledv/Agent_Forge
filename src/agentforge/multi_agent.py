"""History-safe contracts for the bounded support-report agent graph."""

from __future__ import annotations

from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import NonNegativeInt, PositiveInt, field_validator, model_validator

from agentforge.domain import (
    FileReference,
    Goal,
    ModelProfileSnapshot,
    StrictModel,
    _validate_ticket_files,
)


WorkflowKind = Literal["support-report-v1", "support-report-v2"]


class TaskGraphLimits(StrictModel):
    graph_version: Literal[2]
    max_ticket_files: PositiveInt
    max_research_tasks: PositiveInt
    max_concurrent_research: PositiveInt
    max_revisions: NonNegativeInt
    max_role_tasks: PositiveInt
    max_elapsed_seconds: PositiveInt
    max_research_note_chars: PositiveInt
    max_review_feedback_chars: PositiveInt


SUPPORT_REPORT_GRAPH_V2 = TaskGraphLimits(
    graph_version=2,
    max_ticket_files=16,
    max_research_tasks=4,
    max_concurrent_research=2,
    max_revisions=2,
    max_role_tasks=10,
    max_elapsed_seconds=900,
    max_research_note_chars=4_096,
    max_review_feedback_chars=2_048,
)


class ResearchNoteRef(FileReference):
    pass


class ReviewFeedbackRef(FileReference):
    pass


class ReviewDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REVISE = "REVISE"


class ReviewResult(StrictModel):
    decision: ReviewDecision
    feedback: ReviewFeedbackRef | None = None

    @model_validator(mode="after")
    def validate_feedback(self) -> ReviewResult:
        if self.decision == ReviewDecision.REVISE and self.feedback is None:
            raise ValueError("REVISE requires a feedback reference")
        if self.decision == ReviewDecision.ACCEPT and self.feedback is not None:
            raise ValueError("ACCEPT must not include a feedback reference")
        return self


class MultiAgentWorkflowInput(StrictModel):
    run_id: UUID
    goal: Goal
    ticket_files: tuple[str, ...]
    model_profile: ModelProfileSnapshot
    limits: TaskGraphLimits

    _ticket_files_are_relative = field_validator("ticket_files", mode="before")(
        _validate_ticket_files
    )

    @model_validator(mode="after")
    def validate_ticket_count(self) -> MultiAgentWorkflowInput:
        if len(self.ticket_files) > self.limits.max_ticket_files:
            raise ValueError(
                f"ticket_files must contain at most {self.limits.max_ticket_files} paths"
            )
        return self


READ_TICKETS_ORDINAL = 0
PUBLICATION_ORDINAL = 11
APPROVAL_ORDINAL = 12
PRE_DELIVERY_GATE_ORDINAL = 13
DELIVERY_ORDINAL = 14
POST_DELIVERY_GATE_ORDINAL = 15


def partition_ticket_paths(
    ticket_files: tuple[str, ...], limits: TaskGraphLimits
) -> tuple[tuple[str, ...], ...]:
    """Sort paths and distribute them round-robin across bounded shards."""
    paths = _validate_ticket_files(ticket_files)
    if len(paths) > limits.max_ticket_files:
        raise ValueError(
            f"ticket_files must contain at most {limits.max_ticket_files} paths"
        )
    shard_count = min(len(paths), limits.max_research_tasks)
    ordered = tuple(sorted(paths))
    return tuple(ordered[index::shard_count] for index in range(shard_count))


def research_ordinal(shard_index: int) -> int:
    if not 0 <= shard_index < SUPPORT_REPORT_GRAPH_V2.max_research_tasks:
        raise ValueError("research shard index is outside the fixed graph")
    return 1 + shard_index


def writer_ordinal(revision: int) -> int:
    if not 0 <= revision <= SUPPORT_REPORT_GRAPH_V2.max_revisions:
        raise ValueError("writer revision is outside the fixed graph")
    return 5 + 2 * revision


def reviewer_ordinal(review_round: int) -> int:
    if not 0 <= review_round <= SUPPORT_REPORT_GRAPH_V2.max_revisions:
        raise ValueError("review round is outside the fixed graph")
    return 6 + 2 * review_round


def structural_provider_request_limit(
    limits: TaskGraphLimits, *, activity_attempts: int, max_repairs: int
) -> int:
    if isinstance(activity_attempts, bool) or activity_attempts <= 0:
        raise ValueError("activity_attempts must be a positive integer")
    if isinstance(max_repairs, bool) or max_repairs < 0:
        raise ValueError("max_repairs must be a non-negative integer")
    return limits.max_role_tasks * activity_attempts * (1 + max_repairs)
