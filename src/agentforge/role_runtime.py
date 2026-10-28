"""Activity-local contracts for bounded researcher, writer, and reviewer roles."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from agentforge.domain import (
    Goal,
    ModelProfileSnapshot,
    StrictModel,
    _validate_relative_path,
)
from agentforge.model_runtime import ModelUsageReporter
from agentforge.multi_agent import ReviewDecision, SUPPORT_REPORT_GRAPH_V2


FindingText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]
TicketBody = Annotated[str, StringConstraints(max_length=262_144)]
ReportMarkdown = Annotated[str, StringConstraints(min_length=1, max_length=262_144)]
FeedbackText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
]


class VerifiedTicket(StrictModel):
    path: str
    content: TicketBody

    _path_is_relative = field_validator("path")(_validate_relative_path)


class ResearchFinding(StrictModel):
    path: str
    observation: FindingText
    evidence_excerpt: FindingText

    _path_is_relative = field_validator("path")(_validate_relative_path)


class ResearchNoteBody(StrictModel):
    findings: tuple[ResearchFinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_paths(self) -> ResearchNoteBody:
        paths = tuple(finding.path for finding in self.findings)
        if len(paths) != len(set(paths)):
            raise ValueError("research finding paths must be unique")
        return self

    def require_exact_paths(self, assigned_paths: tuple[str, ...]) -> ResearchNoteBody:
        paths = tuple(finding.path for finding in self.findings)
        if paths != assigned_paths:
            raise ValueError("research findings must exactly cover assigned paths")
        return self


class LoadedResearchNote(StrictModel):
    shard_index: NonNegativeInt
    body: ResearchNoteBody

    @model_validator(mode="after")
    def validate_shard_index(self) -> LoadedResearchNote:
        if self.shard_index >= SUPPORT_REPORT_GRAPH_V2.max_research_tasks:
            raise ValueError("research shard index is outside the fixed graph")
        return self


class ReportBody(StrictModel):
    markdown: ReportMarkdown

    @field_validator("markdown")
    @classmethod
    def reject_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("report must not contain NUL")
        return value


class ReviewCategory(str, Enum):
    MISSING_GOAL = "MISSING_GOAL"
    MISSING_TICKET = "MISSING_TICKET"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    UNCLEAR_SUMMARY = "UNCLEAR_SUMMARY"
    FORMAT = "FORMAT"


class ReviewFeedbackItem(StrictModel):
    category: ReviewCategory
    text: FeedbackText


class ReviewFeedbackBody(StrictModel):
    items: tuple[ReviewFeedbackItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_combined_text(self) -> ReviewFeedbackBody:
        combined = sum(len(item.text) for item in self.items)
        if combined > SUPPORT_REPORT_GRAPH_V2.max_review_feedback_chars:
            raise ValueError("combined review feedback exceeds its character limit")
        return self


def _validate_ordered_notes(
    notes: tuple[LoadedResearchNote, ...],
) -> tuple[str, ...]:
    if not notes:
        raise ValueError("at least one research note is required")
    shard_indexes = tuple(note.shard_index for note in notes)
    if shard_indexes != tuple(range(len(notes))):
        raise ValueError("research notes must be in exact shard order")
    paths = tuple(
        finding.path for note in notes for finding in note.body.findings
    )
    if len(paths) != len(set(paths)):
        raise ValueError("research notes must not repeat ticket paths")
    return paths


class ResearchRoleRequest(StrictModel):
    run_id: UUID
    goal: Goal
    tickets: tuple[VerifiedTicket, ...] = Field(min_length=1)
    shard_index: NonNegativeInt
    shard_count: PositiveInt
    graph_version: Literal[2]
    model_profile: ModelProfileSnapshot

    @model_validator(mode="after")
    def validate_assignment(self) -> ResearchRoleRequest:
        if self.shard_count > SUPPORT_REPORT_GRAPH_V2.max_research_tasks:
            raise ValueError("research shard count exceeds the fixed graph")
        if self.shard_index >= self.shard_count:
            raise ValueError("research shard index must be below shard count")
        paths = tuple(ticket.path for ticket in self.tickets)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("assigned ticket paths must be unique and sorted")
        return self

    @property
    def assigned_paths(self) -> tuple[str, ...]:
        return tuple(ticket.path for ticket in self.tickets)


class WriterRoleRequest(StrictModel):
    run_id: UUID
    goal: Goal
    notes: tuple[LoadedResearchNote, ...] = Field(min_length=1)
    previous_report: ReportBody | None = None
    feedback: ReviewFeedbackBody | None = None
    revision: NonNegativeInt
    graph_version: Literal[2]
    model_profile: ModelProfileSnapshot

    @model_validator(mode="after")
    def validate_revision(self) -> WriterRoleRequest:
        _validate_ordered_notes(self.notes)
        if self.revision > SUPPORT_REPORT_GRAPH_V2.max_revisions:
            raise ValueError("writer revision is outside the fixed graph")
        if self.revision == 0:
            if self.previous_report is not None or self.feedback is not None:
                raise ValueError("initial writer request cannot include revision state")
        elif self.previous_report is None or self.feedback is None:
            raise ValueError("writer revision requires prior report and feedback")
        return self

    @property
    def ticket_paths(self) -> tuple[str, ...]:
        return _validate_ordered_notes(self.notes)


class ReviewerRoleRequest(StrictModel):
    run_id: UUID
    goal: Goal
    notes: tuple[LoadedResearchNote, ...] = Field(min_length=1)
    report: ReportBody
    review_round: NonNegativeInt
    graph_version: Literal[2]
    model_profile: ModelProfileSnapshot

    @model_validator(mode="after")
    def validate_review_round(self) -> ReviewerRoleRequest:
        _validate_ordered_notes(self.notes)
        if self.review_round > SUPPORT_REPORT_GRAPH_V2.max_revisions:
            raise ValueError("review round is outside the fixed graph")
        return self

    @property
    def ticket_paths(self) -> tuple[str, ...]:
        return _validate_ordered_notes(self.notes)


class ReviewBody(StrictModel):
    decision: ReviewDecision
    feedback: ReviewFeedbackBody | None = None

    @model_validator(mode="after")
    def validate_feedback(self) -> ReviewBody:
        if self.decision == ReviewDecision.REVISE and self.feedback is None:
            raise ValueError("REVISE requires feedback")
        if self.decision == ReviewDecision.ACCEPT and self.feedback is not None:
            raise ValueError("ACCEPT must not include feedback")
        return self


class RoleProvider(Protocol):
    async def research(
        self, request: ResearchRoleRequest, usage: ModelUsageReporter
    ) -> ResearchNoteBody: ...

    async def write(
        self, request: WriterRoleRequest, usage: ModelUsageReporter
    ) -> ReportBody: ...

    async def review(
        self, request: ReviewerRoleRequest, usage: ModelUsageReporter
    ) -> ReviewBody: ...
