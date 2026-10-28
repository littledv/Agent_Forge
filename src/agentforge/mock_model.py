"""Deterministic, offline model substitute for the first AgentForge milestone."""

from __future__ import annotations

from agentforge.domain import (
    AgentAction,
    DeliverReportAction,
    FinishAction,
    ModelDecisionInput,
    PublishReportAction,
    ReadTicketsAction,
)
from agentforge.model_runtime import ModelUsageReporter
from agentforge.multi_agent import ReviewDecision
from agentforge.role_runtime import (
    ReportBody,
    ResearchFinding,
    ResearchNoteBody,
    ResearchRoleRequest,
    ReviewBody,
    ReviewCategory,
    ReviewFeedbackBody,
    ReviewFeedbackItem,
    ReviewerRoleRequest,
    WriterRoleRequest,
)
from agentforge.storage import ArtifactStorage, StorageViolation


class InvalidModelState(ValueError):
    """The compact Workflow state cannot map to a valid next action."""


class DeterministicMockModel:
    def __init__(self, storage: ArtifactStorage, *, max_draft_bytes: int) -> None:
        if max_draft_bytes <= 0:
            raise ValueError("max_draft_bytes must be positive")
        self._storage = storage
        self._max_draft_bytes = max_draft_bytes

    async def decide(
        self, input: ModelDecisionInput, usage: ModelUsageReporter
    ) -> AgentAction:
        if input.ticket_snapshot is None:
            if (
                input.draft is not None
                or input.artifact is not None
                or input.delivery is not None
            ):
                raise InvalidModelState(
                    "draft, artifact, or delivery requires a ticket snapshot"
                )
            return ReadTicketsAction(
                type="read_tickets", ticket_files=input.ticket_files
            )

        if input.artifact is not None:
            if input.draft is None:
                raise InvalidModelState("artifact requires the draft that produced it")
            if input.delivery is None:
                return DeliverReportAction(
                    type="deliver_report",
                    target="simulated-inbox",
                    artifact=input.artifact,
                )
            if input.delivery.artifact_sha256 != input.artifact.sha256:
                raise InvalidModelState("delivery receipt does not match the artifact")
            return FinishAction(type="finish", artifact=input.artifact)

        if input.delivery is not None:
            raise InvalidModelState("delivery receipt requires an artifact")

        if input.draft is not None:
            return PublishReportAction(
                type="publish_report",
                ticket_snapshot=input.ticket_snapshot,
                draft=input.draft,
            )

        try:
            tickets = self._storage.read_ticket_snapshot(input.ticket_snapshot)
        except StorageViolation as error:
            raise InvalidModelState("ticket snapshot cannot be verified") from error
        snapshot_paths = tuple(ticket["path"] for ticket in tickets)
        if snapshot_paths != tuple(sorted(input.ticket_files)):
            raise InvalidModelState("ticket snapshot does not match the requested files")

        report = self._render_report(input.goal, tickets)
        if len(report) > self._max_draft_bytes:
            raise InvalidModelState("deterministic report exceeds the draft size limit")
        draft = self._storage.write_draft(input.run_id, report)
        return PublishReportAction(
            type="publish_report",
            ticket_snapshot=input.ticket_snapshot,
            draft=draft,
        )

    async def research(
        self, request: ResearchRoleRequest, usage: ModelUsageReporter
    ) -> ResearchNoteBody:
        findings = tuple(
            ResearchFinding(
                path=ticket.path,
                observation=self._first_line(ticket.content),
                evidence_excerpt=self._bounded_excerpt(ticket.content),
            )
            for ticket in request.tickets
        )
        return ResearchNoteBody(findings=findings).require_exact_paths(
            request.assigned_paths
        )

    async def write(
        self, request: WriterRoleRequest, usage: ModelUsageReporter
    ) -> ReportBody:
        lines = [
            "# Support Report",
            "",
            "## Goal",
            "",
            request.goal,
            "",
            f"Ticket count: {len(request.ticket_paths)}",
            "",
        ]
        for note in request.notes:
            for finding in note.body.findings:
                lines.extend(
                    [
                        f"## {finding.path}",
                        "",
                        finding.observation,
                        "",
                        f"Evidence: {finding.evidence_excerpt}",
                        "",
                    ]
                )
        if request.feedback is not None:
            lines.extend([f"## Revision {request.revision}", ""])
            lines.extend(
                f"- {item.category.value}: {item.text}"
                for item in request.feedback.items
            )
            lines.append("")
        lines.extend(
            [
                "## Summary",
                "",
                f"Reviewed {len(request.ticket_paths)} support tickets in shard order.",
                "",
            ]
        )
        markdown = "\n".join(lines)
        max_chars = request.model_profile.max_report_chars
        if max_chars > 0 and len(markdown) > max_chars:
            raise InvalidModelState("deterministic report exceeds the profile limit")
        if len(markdown.encode("utf-8")) > self._max_draft_bytes:
            raise InvalidModelState("deterministic report exceeds the draft size limit")
        return ReportBody(markdown=markdown)

    async def review(
        self, request: ReviewerRoleRequest, usage: ModelUsageReporter
    ) -> ReviewBody:
        feedback: list[ReviewFeedbackItem] = []
        if request.goal not in request.report.markdown:
            feedback.append(
                ReviewFeedbackItem(
                    category=ReviewCategory.MISSING_GOAL,
                    text="Include the submitted goal.",
                )
            )
        missing_paths = tuple(
            path for path in request.ticket_paths if path not in request.report.markdown
        )
        for path in missing_paths:
            feedback.append(
                ReviewFeedbackItem(
                    category=ReviewCategory.MISSING_TICKET,
                    text=f"Include findings for {path}.",
                )
            )
        if feedback:
            return ReviewBody(
                decision=ReviewDecision.REVISE,
                feedback=ReviewFeedbackBody(items=tuple(feedback)),
            )
        return ReviewBody(decision=ReviewDecision.ACCEPT)

    @staticmethod
    def _first_line(content: str) -> str:
        for line in content.splitlines():
            normalized = " ".join(line.split())
            if normalized:
                return normalized[:512]
        return "No ticket content was provided."

    @staticmethod
    def _bounded_excerpt(content: str) -> str:
        normalized = " ".join(content.split())
        return (normalized or "No ticket content was provided.")[:512]

    @staticmethod
    def _render_report(goal: str, tickets: list[dict[str, str]]) -> bytes:
        lines = [
            "# Support Report",
            "",
            "## Goal",
            "",
            goal,
            "",
            f"Ticket count: {len(tickets)}",
            "",
        ]
        for ticket in tickets:
            lines.extend(
                [
                    f"## {ticket['path']}",
                    "",
                    ticket["content"].rstrip("\n"),
                    "",
                ]
            )
        lines.extend(
            [
                "## Summary",
                "",
                f"Reviewed {len(tickets)} support tickets in lexical order.",
                "",
            ]
        )
        return "\n".join(lines).encode("utf-8")
