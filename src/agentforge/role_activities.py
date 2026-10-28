"""History-safe Temporal Activities for bounded report roles."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import NonNegativeInt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agentforge.activities import DurableModelUsageReporter
from agentforge.domain import (
    ArtifactRef,
    AttemptState,
    DraftRef,
    FailureKind,
    Goal,
    ModelIdentity,
    ModelProfileSnapshot,
    StepState,
    StrictModel,
    TicketSnapshotRef,
)
from agentforge.model_runtime import ModelUsageReporter, NoOpModelUsageReporter
from agentforge.multi_agent import (
    ResearchNoteRef,
    ReviewDecision,
    ReviewFeedbackRef,
    ReviewResult,
)
from agentforge.ollama_model import (
    ModelBudgetExceeded,
    ModelConfigurationError,
    ModelOutputInvalid,
    ModelRequestRejected,
    ModelTransportError,
)
from agentforge.repository import CancellationRepository, record_activity_attempt
from agentforge.role_runtime import (
    LoadedResearchNote,
    ReportBody,
    ResearchNoteBody,
    ResearchRoleRequest,
    ReviewFeedbackBody,
    ReviewerRoleRequest,
    RoleProvider,
    VerifiedTicket,
    WriterRoleRequest,
)
from agentforge.storage import ArtifactStorage, StorageViolation


class ResearchActivityInput(StrictModel):
    run_id: UUID
    goal: Goal
    ticket_snapshot: TicketSnapshotRef
    assigned_paths: tuple[str, ...]
    shard_index: NonNegativeInt
    shard_count: int
    step_ordinal: NonNegativeInt
    model_profile: ModelProfileSnapshot


class WriteReportActivityInput(StrictModel):
    run_id: UUID
    goal: Goal
    research_notes: tuple[ResearchNoteRef, ...]
    revision: NonNegativeInt
    step_ordinal: NonNegativeInt
    model_profile: ModelProfileSnapshot
    previous_draft: DraftRef | None = None
    review_feedback: ReviewFeedbackRef | None = None


class ReviewReportActivityInput(StrictModel):
    run_id: UUID
    goal: Goal
    research_notes: tuple[ResearchNoteRef, ...]
    draft: DraftRef
    review_round: NonNegativeInt
    step_ordinal: NonNegativeInt
    model_profile: ModelProfileSnapshot


class PublishReviewedReportInput(StrictModel):
    run_id: UUID
    draft: DraftRef
    step_ordinal: NonNegativeInt


class CheckCancellationInput(StrictModel):
    run_id: UUID


class RoleActivities:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: ArtifactStorage,
        roles: RoleProvider,
        model_profile: ModelProfileSnapshot,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._roles = roles
        self._model_profile = model_profile

    async def _record(
        self,
        *,
        run_id: UUID,
        ordinal: int,
        name: str,
        attempt_state: AttemptState,
        step_state: StepState,
        failure_kind: FailureKind | None = None,
    ) -> None:
        async with self._sessions() as session:
            await record_activity_attempt(
                session,
                run_id=run_id,
                ordinal=ordinal,
                name=name,
                attempt_number=activity.info().attempt,
                attempt_state=attempt_state,
                step_state=step_state,
                failure_kind=failure_kind,
            )

    def _usage(self, run_id: UUID, ordinal: int) -> ModelUsageReporter:
        if self._model_profile.provider == "mock":
            return NoOpModelUsageReporter()
        return DurableModelUsageReporter(
            self._sessions,
            run_id=run_id,
            ordinal=ordinal,
            attempt_number=activity.info().attempt,
            identity=ModelIdentity(
                provider=self._model_profile.provider,
                model_name=self._model_profile.model_name,
                model_digest=self._model_profile.model_digest,
            ),
        )

    @staticmethod
    async def _run_with_heartbeat(
        operation: Callable[[ModelUsageReporter], Awaitable[object]],
        usage: ModelUsageReporter,
    ) -> object:
        task = asyncio.create_task(operation(usage))
        try:
            while not task.done():
                activity.heartbeat()
                await asyncio.wait({task}, timeout=1)
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def _execute(
        self,
        *,
        run_id: UUID,
        ordinal: int,
        name: str,
        model_profile: ModelProfileSnapshot | None,
        operation: Callable[[ModelUsageReporter], Awaitable[object]],
    ) -> object:
        await self._record(
            run_id=run_id,
            ordinal=ordinal,
            name=name,
            attempt_state=AttemptState.RUNNING,
            step_state=StepState.RUNNING,
        )
        try:
            if model_profile is not None and model_profile != self._model_profile:
                raise ModelConfigurationError("model profile mismatch")
            result = await self._run_with_heartbeat(
                operation, self._usage(run_id, ordinal)
            )
        except ModelBudgetExceeded:
            await self._record_failure(run_id, ordinal, name, permanent=True)
            raise ApplicationError(
                "model budget exceeded",
                type="ModelBudgetExceeded",
                non_retryable=True,
            ) from None
        except ModelTransportError:
            await self._record_failure(run_id, ordinal, name, permanent=False)
            raise ApplicationError(
                "model provider unavailable",
                type="ModelTransportError",
                non_retryable=False,
            ) from None
        except (
            StorageViolation,
            UnicodeDecodeError,
            ModelConfigurationError,
            ModelRequestRejected,
            ModelOutputInvalid,
            ValueError,
        ) as error:
            await self._record_failure(run_id, ordinal, name, permanent=True)
            raise ApplicationError(
                "report role rejected", type=type(error).__name__, non_retryable=True
            ) from None
        except Exception as error:
            await self._record_failure(run_id, ordinal, name, permanent=False)
            raise ApplicationError(
                "report role failed", type=type(error).__name__, non_retryable=False
            ) from None
        await self._record(
            run_id=run_id,
            ordinal=ordinal,
            name=name,
            attempt_state=AttemptState.SUCCEEDED,
            step_state=StepState.SUCCEEDED,
        )
        return result

    async def _record_failure(
        self, run_id: UUID, ordinal: int, name: str, *, permanent: bool
    ) -> None:
        await self._record(
            run_id=run_id,
            ordinal=ordinal,
            name=name,
            attempt_state=AttemptState.FAILED,
            step_state=StepState.FAILED if permanent else StepState.RETRY_SCHEDULED,
            failure_kind=FailureKind.PERMANENT if permanent else FailureKind.RETRYABLE,
        )

    def _load_notes(
        self, run_id: UUID, refs: tuple[ResearchNoteRef, ...]
    ) -> tuple[LoadedResearchNote, ...]:
        notes: list[LoadedResearchNote] = []
        for shard_index, ref in enumerate(refs):
            expected_segment = f"/shard-{shard_index}/"
            if expected_segment not in f"/{ref.path}":
                raise StorageViolation("research note order does not match shard paths")
            content = self._storage.read_research_note(run_id, ref)
            notes.append(
                LoadedResearchNote(
                    shard_index=shard_index,
                    body=ResearchNoteBody.model_validate_json(content),
                )
            )
        return tuple(notes)

    @activity.defn(name="research_role")
    async def research_role(self, input: ResearchActivityInput) -> ResearchNoteRef:
        name = f"research_shard_{input.shard_index + 1}"

        async def operation(usage: ModelUsageReporter) -> ResearchNoteRef:
            tickets = self._storage.read_ticket_snapshot(input.ticket_snapshot)
            by_path = {ticket["path"]: ticket["content"] for ticket in tickets}
            if any(path not in by_path for path in input.assigned_paths):
                raise StorageViolation("research assignment is outside ticket snapshot")
            request = ResearchRoleRequest(
                run_id=input.run_id,
                goal=input.goal,
                tickets=tuple(
                    VerifiedTicket(path=path, content=by_path[path])
                    for path in input.assigned_paths
                ),
                shard_index=input.shard_index,
                shard_count=input.shard_count,
                graph_version=2,
                model_profile=input.model_profile,
            )
            body = await self._roles.research(request, usage)
            encoded = body.model_dump_json().encode("utf-8")
            return self._storage.write_research_note(
                input.run_id,
                input.shard_index,
                encoded,
                max_chars=4_096,
            )

        return await self._execute(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name=name,
            model_profile=input.model_profile,
            operation=operation,
        )  # type: ignore[return-value]

    @activity.defn(name="write_report_role")
    async def write_report_role(self, input: WriteReportActivityInput) -> DraftRef:
        name = f"write_report_r{input.revision}"

        async def operation(usage: ModelUsageReporter) -> DraftRef:
            notes = self._load_notes(input.run_id, input.research_notes)
            previous = None
            if input.previous_draft is not None:
                previous = ReportBody(
                    markdown=self._storage.read_revision_draft(
                        input.run_id, input.previous_draft
                    ).decode("utf-8")
                )
            feedback = None
            if input.review_feedback is not None:
                feedback = ReviewFeedbackBody.model_validate_json(
                    self._storage.read_review_feedback(
                        input.run_id, input.review_feedback
                    )
                )
            request = WriterRoleRequest(
                run_id=input.run_id,
                goal=input.goal,
                notes=notes,
                previous_report=previous,
                feedback=feedback,
                revision=input.revision,
                graph_version=2,
                model_profile=input.model_profile,
            )
            body = await self._roles.write(request, usage)
            return self._storage.write_revision_draft(
                input.run_id,
                input.revision,
                body.markdown.encode("utf-8"),
                max_chars=(
                    input.model_profile.max_report_chars
                    or self._storage.max_total_ticket_bytes
                ),
            )

        return await self._execute(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name=name,
            model_profile=input.model_profile,
            operation=operation,
        )  # type: ignore[return-value]

    @activity.defn(name="review_report_role")
    async def review_report_role(self, input: ReviewReportActivityInput) -> ReviewResult:
        name = f"review_report_r{input.review_round}"

        async def operation(usage: ModelUsageReporter) -> ReviewResult:
            notes = self._load_notes(input.run_id, input.research_notes)
            report = ReportBody(
                markdown=self._storage.read_revision_draft(
                    input.run_id, input.draft
                ).decode("utf-8")
            )
            body = await self._roles.review(
                ReviewerRoleRequest(
                    run_id=input.run_id,
                    goal=input.goal,
                    notes=notes,
                    report=report,
                    review_round=input.review_round,
                    graph_version=2,
                    model_profile=input.model_profile,
                ),
                usage,
            )
            if body.decision == ReviewDecision.ACCEPT:
                return ReviewResult(decision=ReviewDecision.ACCEPT)
            encoded = body.feedback.model_dump_json().encode("utf-8")  # type: ignore[union-attr]
            feedback = self._storage.write_review_feedback(
                input.run_id,
                input.review_round,
                encoded,
                max_chars=2_048,
            )
            return ReviewResult(decision=ReviewDecision.REVISE, feedback=feedback)

        return await self._execute(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name=name,
            model_profile=input.model_profile,
            operation=operation,
        )  # type: ignore[return-value]

    @activity.defn(name="publish_reviewed_report")
    async def publish_reviewed_report(
        self, input: PublishReviewedReportInput
    ) -> ArtifactRef:
        async def operation(usage: ModelUsageReporter) -> ArtifactRef:
            return self._storage.publish_reviewed_report(input.run_id, input.draft)

        return await self._execute(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name="publish_reviewed_report",
            model_profile=None,
            operation=operation,
        )  # type: ignore[return-value]

    @activity.defn(name="check_cancellation")
    async def check_cancellation(self, input: CheckCancellationInput) -> bool:
        async with self._sessions() as session:
            return await CancellationRepository.exists(session, input.run_id)
