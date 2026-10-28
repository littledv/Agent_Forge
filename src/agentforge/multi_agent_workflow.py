"""Deterministic coordination for the bounded support-report version-2 graph."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    import annotated_types

    from agentforge.activities import (
        CreateDeliveryApprovalInput,
        DeliverReportInput,
        DeliveryGateResult,
        ReadTicketsInput,
        ResolveDeliveryGateInput,
    )
    from agentforge.domain import (
        ApprovalSignal,
        ApprovalSnapshot,
        ApprovalState,
        ArtifactRef,
        CancellationSignal,
        DeliverReportAction,
        DeliveryReceipt,
        DraftRef,
        FailureKind,
        ProjectionEvent,
        RunState,
        TicketSnapshotRef,
    )
    from agentforge.multi_agent import (
        APPROVAL_ORDINAL,
        DELIVERY_ORDINAL,
        POST_DELIVERY_GATE_ORDINAL,
        PRE_DELIVERY_GATE_ORDINAL,
        PUBLICATION_ORDINAL,
        READ_TICKETS_ORDINAL,
        MultiAgentWorkflowInput,
        ResearchNoteRef,
        ReviewDecision,
        ReviewFeedbackRef,
        ReviewResult,
        partition_ticket_paths,
        research_ordinal,
        reviewer_ordinal,
        writer_ordinal,
    )
    from agentforge.policy import authorize_delivery
    from agentforge.role_activities import (
        CheckCancellationInput,
        PublishReviewedReportInput,
        ResearchActivityInput,
        ReviewReportActivityInput,
        WriteReportActivityInput,
    )
    from agentforge.workflow import ACTIVITY_RETRY, ACTIVITY_TIMEOUT, PROJECTION_RETRY


ROLE_TIMEOUT = timedelta(seconds=180)
ROLE_HEARTBEAT_TIMEOUT = timedelta(seconds=5)


@workflow.defn(name="MultiAgentSupportReportWorkflow")
class MultiAgentSupportReportWorkflow:
    def __init__(self) -> None:
        self._run_id = None
        self._approval_id = None
        self._action_hash = None
        self._approval_signal_received = False
        self._cancellation_signal_received = False
        self._seen_message_ids = set()
        self._sequence = 0

    @workflow.signal
    def approval_decided(self, signal: ApprovalSignal) -> None:
        if signal.message_id in self._seen_message_ids:
            return
        self._seen_message_ids.add(signal.message_id)
        if signal.approval_id == self._approval_id and signal.action_hash == self._action_hash:
            self._approval_signal_received = True

    @workflow.signal
    def cancellation_requested(self, signal: CancellationSignal) -> None:
        if signal.message_id in self._seen_message_ids:
            return
        self._seen_message_ids.add(signal.message_id)
        if signal.run_id == self._run_id:
            self._cancellation_signal_received = True

    @workflow.run
    async def run(self, input: MultiAgentWorkflowInput) -> ArtifactRef:
        self._run_id = input.run_id
        deadline = workflow.now() + timedelta(seconds=input.limits.max_elapsed_seconds)
        await self._set_state(input.run_id, RunState.RUNNING)
        role_count = 0
        publication_complete = False
        try:
            await self._guard(input.run_id, deadline)
            remaining = self._remaining_graph_time(deadline)
            snapshot = await workflow.execute_activity(
                "read_tickets",
                ReadTicketsInput(
                    run_id=input.run_id,
                    step_ordinal=READ_TICKETS_ORDINAL,
                    ticket_files=input.ticket_files,
                ),
                result_type=TicketSnapshotRef,
                start_to_close_timeout=min(ACTIVITY_TIMEOUT, remaining),
                schedule_to_close_timeout=remaining,
                retry_policy=ACTIVITY_RETRY,
            )
            await self._guard(input.run_id, deadline)

            shards = partition_ticket_paths(input.ticket_files, input.limits)
            note_slots: list[ResearchNoteRef | None] = [None] * len(shards)
            for start in range(0, len(shards), input.limits.max_concurrent_research):
                await self._guard(input.run_id, deadline)
                batch_indexes = tuple(
                    range(
                        start,
                        min(start + input.limits.max_concurrent_research, len(shards)),
                    )
                )
                role_count += len(batch_indexes)
                await self._require_role_budget(
                    input.run_id, role_count, input.limits.max_role_tasks
                )
                timeout = self._remaining_timeout(deadline)
                remaining = self._remaining_graph_time(deadline)
                results = await asyncio.gather(
                    *(
                        workflow.execute_activity(
                            "research_role",
                            ResearchActivityInput(
                                run_id=input.run_id,
                                goal=input.goal,
                                ticket_snapshot=snapshot,
                                assigned_paths=shards[index],
                                shard_index=index,
                                shard_count=len(shards),
                                step_ordinal=research_ordinal(index),
                                model_profile=input.model_profile,
                            ),
                            result_type=ResearchNoteRef,
                            start_to_close_timeout=timeout,
                            schedule_to_close_timeout=remaining,
                            heartbeat_timeout=min(ROLE_HEARTBEAT_TIMEOUT, timeout),
                            retry_policy=ACTIVITY_RETRY,
                        )
                        for index in batch_indexes
                    )
                )
                for index, result in zip(batch_indexes, results):
                    note_slots[index] = result
                await self._guard(input.run_id, deadline)
            notes = tuple(note for note in note_slots if note is not None)

            previous_draft = None
            feedback: ReviewFeedbackRef | None = None
            accepted_draft = None
            for revision in range(input.limits.max_revisions + 1):
                await self._guard(input.run_id, deadline)
                role_count += 1
                await self._require_role_budget(
                    input.run_id, role_count, input.limits.max_role_tasks
                )
                timeout = self._remaining_timeout(deadline)
                remaining = self._remaining_graph_time(deadline)
                draft = await workflow.execute_activity(
                    "write_report_role",
                    WriteReportActivityInput(
                        run_id=input.run_id,
                        goal=input.goal,
                        research_notes=notes,
                        previous_draft=previous_draft,
                        review_feedback=feedback,
                        revision=revision,
                        step_ordinal=writer_ordinal(revision),
                        model_profile=input.model_profile,
                    ),
                    result_type=DraftRef,
                    start_to_close_timeout=timeout,
                    schedule_to_close_timeout=remaining,
                    heartbeat_timeout=min(ROLE_HEARTBEAT_TIMEOUT, timeout),
                    retry_policy=ACTIVITY_RETRY,
                )
                await self._guard(input.run_id, deadline)
                role_count += 1
                await self._require_role_budget(
                    input.run_id, role_count, input.limits.max_role_tasks
                )
                timeout = self._remaining_timeout(deadline)
                remaining = self._remaining_graph_time(deadline)
                review = await workflow.execute_activity(
                    "review_report_role",
                    ReviewReportActivityInput(
                        run_id=input.run_id,
                        goal=input.goal,
                        research_notes=notes,
                        draft=draft,
                        review_round=revision,
                        step_ordinal=reviewer_ordinal(revision),
                        model_profile=input.model_profile,
                    ),
                    result_type=ReviewResult,
                    start_to_close_timeout=timeout,
                    schedule_to_close_timeout=remaining,
                    heartbeat_timeout=min(ROLE_HEARTBEAT_TIMEOUT, timeout),
                    retry_policy=ACTIVITY_RETRY,
                )
                await self._guard(input.run_id, deadline)
                if review.decision == ReviewDecision.ACCEPT:
                    accepted_draft = draft
                    break
                previous_draft = draft
                feedback = review.feedback
            if accepted_draft is None:
                await self._budget_exhausted(input.run_id, "revision budget exhausted")

            await self._guard(input.run_id, deadline)
            timeout = self._remaining_timeout(deadline)
            remaining = self._remaining_graph_time(deadline)
            artifact = await workflow.execute_activity(
                "publish_reviewed_report",
                PublishReviewedReportInput(
                    run_id=input.run_id,
                    draft=accepted_draft,
                    step_ordinal=PUBLICATION_ORDINAL,
                ),
                result_type=ArtifactRef,
                start_to_close_timeout=timeout,
                schedule_to_close_timeout=remaining,
                heartbeat_timeout=min(ROLE_HEARTBEAT_TIMEOUT, timeout),
                retry_policy=ACTIVITY_RETRY,
            )
            publication_complete = True
            await self._check_cancellation(input.run_id)

            action = DeliverReportAction(
                type="deliver_report", target="simulated-inbox", artifact=artifact
            )
            authorization = authorize_delivery(input.run_id, action, artifact)
            self._approval_id = authorization.approval_id
            self._action_hash = authorization.action_hash
            approval = await workflow.execute_activity(
                "create_delivery_approval",
                CreateDeliveryApprovalInput(
                    run_id=input.run_id,
                    step_ordinal=APPROVAL_ORDINAL,
                    authorization=authorization,
                ),
                result_type=ApprovalSnapshot,
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY,
            )
            await self._set_state(input.run_id, RunState.WAITING_APPROVAL, artifact=artifact)
            gate = await self._wait_for_gate(input.run_id, approval.expires_at)
            await self._enforce_gate(input.run_id, gate)
            await self._set_state(input.run_id, RunState.RUNNING)
            gate = await self._resolve_gate(input.run_id, PRE_DELIVERY_GATE_ORDINAL)
            await self._enforce_gate(input.run_id, gate)
            try:
                await workflow.execute_activity(
                    "deliver_report",
                    DeliverReportInput(
                        run_id=input.run_id,
                        step_ordinal=DELIVERY_ORDINAL,
                        authorization=authorization,
                    ),
                    result_type=DeliveryReceipt,
                    start_to_close_timeout=ACTIVITY_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY,
                )
            except ActivityError as error:
                await self._set_state(
                    input.run_id,
                    RunState.NEEDS_ATTENTION,
                    failure_kind=FailureKind.UNCERTAIN,
                    artifact=artifact,
                )
                raise ApplicationError(
                    "delivery could not be reconciled",
                    type="DeliveryNeedsAttention",
                    non_retryable=True,
                ) from error
            gate = await self._resolve_gate(input.run_id, POST_DELIVERY_GATE_ORDINAL)
            if gate.cancellation_requested:
                await self._terminate(
                    input.run_id,
                    RunState.CANCELLED,
                    FailureKind.CANCELLED,
                    "run was cancelled after delivery",
                    "RunCancelled",
                )
            await self._set_state(input.run_id, RunState.SUCCEEDED, artifact=artifact)
            return artifact
        except ActivityError as error:
            cause = error.cause
            if not publication_complete and (
                workflow.now() >= deadline
                or (
                    isinstance(cause, ApplicationError)
                    and cause.type == "ModelBudgetExceeded"
                )
            ):
                await self._set_state(input.run_id, RunState.BUDGET_EXHAUSTED)
                raise
            failure_kind = (
                FailureKind.PERMANENT
                if isinstance(cause, ApplicationError) and cause.non_retryable
                else FailureKind.RETRYABLE
            )
            await self._set_state(
                input.run_id, RunState.FAILED, failure_kind=failure_kind
            )
            raise

    async def _guard(self, run_id, deadline) -> None:
        if workflow.now() >= deadline:
            await self._budget_exhausted(run_id, "graph deadline exhausted")
        remaining = self._remaining_graph_time(deadline)
        await self._check_cancellation(run_id, remaining=remaining)

    async def _check_cancellation(
        self,
        run_id,
        remaining: timedelta | None = None,
    ) -> None:
        if remaining is None:
            cancelled = await workflow.execute_activity(
                "check_cancellation",
                CheckCancellationInput(run_id=run_id),
                result_type=bool,
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=ACTIVITY_RETRY,
            )
        else:
            cancelled = await workflow.execute_activity(
                "check_cancellation",
                CheckCancellationInput(run_id=run_id),
                result_type=bool,
                start_to_close_timeout=min(ACTIVITY_TIMEOUT, remaining),
                schedule_to_close_timeout=remaining,
                retry_policy=ACTIVITY_RETRY,
            )
        if cancelled:
            await self._terminate(
                run_id,
                RunState.CANCELLED,
                FailureKind.CANCELLED,
                "run was cancelled",
                "RunCancelled",
            )

    async def _require_role_budget(self, run_id, count: int, maximum: int) -> None:
        if count > maximum:
            await self._budget_exhausted(run_id, "role task budget exhausted")

    @staticmethod
    def _remaining_timeout(deadline) -> timedelta:
        return min(
            ROLE_TIMEOUT,
            MultiAgentSupportReportWorkflow._remaining_graph_time(deadline),
        )

    @staticmethod
    def _remaining_graph_time(deadline) -> timedelta:
        remaining = deadline - workflow.now()
        if remaining <= timedelta():
            return timedelta(milliseconds=1)
        return remaining

    async def _budget_exhausted(self, run_id, message: str) -> None:
        await self._set_state(run_id, RunState.BUDGET_EXHAUSTED)
        raise ApplicationError(message, type="GraphBudgetExhausted", non_retryable=True)

    async def _wait_for_gate(self, run_id, expires_at) -> DeliveryGateResult:
        while True:
            remaining = expires_at - workflow.now()
            if remaining > timedelta():
                try:
                    await workflow.wait_condition(
                        lambda: self._approval_signal_received
                        or self._cancellation_signal_received,
                        timeout=remaining,
                    )
                except TimeoutError:
                    pass
            gate = await self._resolve_gate(run_id, PRE_DELIVERY_GATE_ORDINAL)
            if gate.cancellation_requested or gate.approval.state is not ApprovalState.PENDING:
                return gate
            self._approval_signal_received = False
            self._cancellation_signal_received = False

    async def _resolve_gate(self, run_id, ordinal: int) -> DeliveryGateResult:
        return await workflow.execute_activity(
            "resolve_delivery_gate",
            ResolveDeliveryGateInput(
                run_id=run_id,
                step_ordinal=ordinal,
                approval_id=self._approval_id,
                action_hash=self._action_hash,
            ),
            result_type=DeliveryGateResult,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY,
        )

    async def _enforce_gate(self, run_id, gate: DeliveryGateResult) -> None:
        if gate.cancellation_requested:
            await self._terminate(
                run_id,
                RunState.CANCELLED,
                FailureKind.CANCELLED,
                "run was cancelled before delivery",
                "RunCancelled",
            )
        if gate.approval.state in {ApprovalState.REJECTED, ApprovalState.EXPIRED}:
            await self._terminate(
                run_id,
                RunState.DENIED,
                FailureKind.DENIED,
                "report delivery was not approved",
                "DeliveryDenied",
            )
        if gate.approval.state is not ApprovalState.APPROVED:
            await self._terminate(
                run_id,
                RunState.DENIED,
                FailureKind.DENIED,
                "report delivery approval did not resolve",
                "DeliveryDenied",
            )

    async def _terminate(
        self, run_id, state, failure_kind, message: str, error_type: str
    ) -> None:
        await self._set_state(run_id, state, failure_kind=failure_kind)
        raise ApplicationError(message, type=error_type, non_retryable=True)

    async def _set_state(
        self,
        run_id,
        state: RunState,
        *,
        failure_kind: FailureKind | None = None,
        artifact: ArtifactRef | None = None,
    ) -> None:
        self._sequence += 1
        await workflow.execute_activity(
            "project_event",
            ProjectionEvent(
                event_id=workflow.uuid4(),
                run_id=run_id,
                sequence=self._sequence,
                run_state=state,
                failure_kind=failure_kind,
                artifact=artifact,
            ),
            result_type=bool,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=PROJECTION_RETRY,
        )
