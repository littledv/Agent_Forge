"""Deterministic Temporal coordination for support-report runs."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    import annotated_types  # Imported up front for Pydantic's sandbox-safe reuse.

    from agentforge.activities import (
        CreateDeliveryApprovalInput,
        DecideActionInput,
        DeliverReportInput,
        DeliveryGateResult,
        PublishReportInput,
        ReadTicketsInput,
        ResolveDeliveryGateInput,
    )
    from agentforge.domain import (
        AgentAction,
        ApprovalSignal,
        ApprovalSnapshot,
        ApprovalState,
        ArtifactRef,
        CancellationSignal,
        DeliverReportAction,
        DeliveryReceipt,
        FailureKind,
        FinishAction,
        ModelDecisionInput,
        ProjectionEvent,
        PublishReportAction,
        ReadTicketsAction,
        RunState,
        TicketSnapshotRef,
        WorkflowInput,
    )
    from agentforge.policy import UnauthorizedDeliveryAction, authorize_delivery


ACTIVITY_TIMEOUT = timedelta(seconds=10)
MODEL_ACTIVITY_TIMEOUT = timedelta(seconds=180)
ACTIVITY_RETRY = RetryPolicy(
    initial_interval=timedelta(milliseconds=200),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=2),
    maximum_attempts=3,
)
PROJECTION_RETRY = RetryPolicy(
    initial_interval=timedelta(milliseconds=200),
    maximum_interval=timedelta(seconds=2),
    maximum_attempts=5,
)


@workflow.defn
class SupportReportWorkflow:
    def __init__(self) -> None:
        self._run_id = None
        self._approval_id = None
        self._action_hash = None
        self._approval_signal_received = False
        self._cancellation_signal_received = False
        self._seen_message_ids = set()

    @workflow.signal
    def approval_decided(self, signal: ApprovalSignal) -> None:
        if signal.message_id in self._seen_message_ids:
            return
        self._seen_message_ids.add(signal.message_id)
        if (
            signal.approval_id == self._approval_id
            and signal.action_hash == self._action_hash
        ):
            self._approval_signal_received = True

    @workflow.signal
    def cancellation_requested(self, signal: CancellationSignal) -> None:
        if signal.message_id in self._seen_message_ids:
            return
        self._seen_message_ids.add(signal.message_id)
        if signal.run_id == self._run_id:
            self._cancellation_signal_received = True

    @workflow.run
    async def run(self, input: WorkflowInput) -> ArtifactRef:
        self._run_id = input.run_id
        sequence = 1
        await self._project(input.run_id, sequence, RunState.RUNNING)
        step_ordinal = 0
        ticket_snapshot = None
        draft = None
        artifact = None
        delivery = None
        delivery_authorization = None

        try:
            for decision_number in range(1, input.max_decisions + 1):
                action = await workflow.execute_activity(
                    "decide_action",
                    DecideActionInput(
                        decision=ModelDecisionInput(
                            run_id=input.run_id,
                            goal=input.goal,
                            ticket_files=input.ticket_files,
                            decision_number=decision_number,
                            ticket_snapshot=ticket_snapshot,
                            draft=draft,
                            artifact=artifact,
                            delivery=delivery,
                            model_profile=input.model_profile,
                        ),
                        step_ordinal=step_ordinal,
                    ),
                    result_type=AgentAction,
                    start_to_close_timeout=MODEL_ACTIVITY_TIMEOUT,
                    retry_policy=ACTIVITY_RETRY,
                )
                step_ordinal += 1

                if isinstance(action, ReadTicketsAction):
                    if action.ticket_files != input.ticket_files:
                        sequence += 1
                        await self._reject_action(
                            input.run_id,
                            sequence,
                            "ticket read is outside the submitted allowlist",
                        )
                    ticket_snapshot = await workflow.execute_activity(
                        "read_tickets",
                        ReadTicketsInput(
                            run_id=input.run_id,
                            step_ordinal=step_ordinal,
                            ticket_files=action.ticket_files,
                        ),
                        result_type=TicketSnapshotRef,
                        start_to_close_timeout=ACTIVITY_TIMEOUT,
                        retry_policy=ACTIVITY_RETRY,
                    )
                    step_ordinal += 1
                    continue

                if isinstance(action, DeliverReportAction):
                    if artifact is None:
                        sequence += 1
                        await self._reject_action(
                            input.run_id,
                            sequence,
                            "delivery requires a published artifact",
                        )
                    try:
                        authorization = authorize_delivery(
                            input.run_id, action, artifact
                        )
                    except UnauthorizedDeliveryAction:
                        sequence += 1
                        await self._reject_action(
                            input.run_id,
                            sequence,
                            "delivery action does not match the published artifact",
                        )
                    delivery_authorization = authorization

                    self._approval_id = authorization.approval_id
                    self._action_hash = authorization.action_hash
                    approval = await workflow.execute_activity(
                        "create_delivery_approval",
                        CreateDeliveryApprovalInput(
                            run_id=input.run_id,
                            step_ordinal=step_ordinal,
                            authorization=authorization,
                        ),
                        result_type=ApprovalSnapshot,
                        start_to_close_timeout=ACTIVITY_TIMEOUT,
                        retry_policy=ACTIVITY_RETRY,
                    )
                    step_ordinal += 1
                    sequence += 1
                    await self._project(
                        input.run_id,
                        sequence,
                        RunState.WAITING_APPROVAL,
                        artifact=artifact,
                    )

                    gate = await self._wait_for_delivery_gate(
                        input.run_id, approval.expires_at, step_ordinal
                    )
                    step_ordinal += 1
                    if gate.cancellation_requested:
                        sequence += 1
                        await self._terminate(
                            input.run_id,
                            sequence,
                            RunState.CANCELLED,
                            FailureKind.CANCELLED,
                            "run was cancelled before delivery",
                            "RunCancelled",
                        )
                    if gate.approval.state in {
                        ApprovalState.REJECTED,
                        ApprovalState.EXPIRED,
                    }:
                        sequence += 1
                        await self._terminate(
                            input.run_id,
                            sequence,
                            RunState.DENIED,
                            FailureKind.DENIED,
                            "report delivery was not approved",
                            "DeliveryDenied",
                        )
                    if gate.approval.state is not ApprovalState.APPROVED:
                        sequence += 1
                        await self._terminate(
                            input.run_id,
                            sequence,
                            RunState.DENIED,
                            FailureKind.DENIED,
                            "report delivery approval did not resolve",
                            "DeliveryDenied",
                        )

                    sequence += 1
                    await self._project(input.run_id, sequence, RunState.RUNNING)
                    gate = await self._resolve_delivery_gate(
                        input.run_id,
                        step_ordinal,
                        authorization.approval_id,
                        authorization.action_hash,
                    )
                    step_ordinal += 1
                    if gate.cancellation_requested:
                        sequence += 1
                        await self._terminate(
                            input.run_id,
                            sequence,
                            RunState.CANCELLED,
                            FailureKind.CANCELLED,
                            "run was cancelled before delivery",
                            "RunCancelled",
                        )
                    try:
                        delivery = await workflow.execute_activity(
                            "deliver_report",
                            DeliverReportInput(
                                run_id=input.run_id,
                                step_ordinal=step_ordinal,
                                authorization=authorization,
                            ),
                            result_type=DeliveryReceipt,
                            start_to_close_timeout=ACTIVITY_TIMEOUT,
                            retry_policy=ACTIVITY_RETRY,
                        )
                    except ActivityError as error:
                        sequence += 1
                        await self._project(
                            input.run_id,
                            sequence,
                            RunState.NEEDS_ATTENTION,
                            failure_kind=FailureKind.UNCERTAIN,
                            artifact=artifact,
                        )
                        raise ApplicationError(
                            "delivery could not be reconciled",
                            type="DeliveryNeedsAttention",
                            non_retryable=True,
                        ) from error
                    step_ordinal += 1

                    gate = await workflow.execute_activity(
                        "resolve_delivery_gate",
                        ResolveDeliveryGateInput(
                            run_id=input.run_id,
                            step_ordinal=step_ordinal,
                            approval_id=authorization.approval_id,
                            action_hash=authorization.action_hash,
                        ),
                        result_type=DeliveryGateResult,
                        start_to_close_timeout=ACTIVITY_TIMEOUT,
                        retry_policy=ACTIVITY_RETRY,
                    )
                    step_ordinal += 1
                    if gate.cancellation_requested:
                        sequence += 1
                        await self._terminate(
                            input.run_id,
                            sequence,
                            RunState.CANCELLED,
                            FailureKind.CANCELLED,
                            "run was cancelled after delivery",
                            "RunCancelled",
                        )
                    continue

                if isinstance(action, PublishReportAction):
                    if (
                        ticket_snapshot is None
                        or action.ticket_snapshot != ticket_snapshot
                    ):
                        sequence += 1
                        await self._reject_action(
                            input.run_id,
                            sequence,
                            "publish action does not match the trusted snapshot",
                        )
                    draft = action.draft
                    artifact = await workflow.execute_activity(
                        "publish_report",
                        PublishReportInput(
                            run_id=input.run_id,
                            step_ordinal=step_ordinal,
                            draft=action.draft,
                        ),
                        result_type=ArtifactRef,
                        start_to_close_timeout=ACTIVITY_TIMEOUT,
                        retry_policy=ACTIVITY_RETRY,
                    )
                    step_ordinal += 1
                    continue

                if isinstance(action, FinishAction):
                    if artifact is None or action.artifact != artifact:
                        sequence += 1
                        await self._reject_action(
                            input.run_id,
                            sequence,
                            "finish action does not match the published artifact",
                        )
                    if delivery_authorization is not None:
                        gate = await self._resolve_delivery_gate(
                            input.run_id,
                            step_ordinal,
                            delivery_authorization.approval_id,
                            delivery_authorization.action_hash,
                        )
                        step_ordinal += 1
                        if gate.cancellation_requested:
                            sequence += 1
                            await self._terminate(
                                input.run_id,
                                sequence,
                                RunState.CANCELLED,
                                FailureKind.CANCELLED,
                                "run was cancelled after delivery",
                                "RunCancelled",
                            )
                    sequence += 1
                    await self._project(
                        input.run_id,
                        sequence,
                        RunState.SUCCEEDED,
                        artifact=action.artifact,
                    )
                    return action.artifact

                raise ApplicationError(
                    "unsupported agent action",
                    type="UnsupportedAgentAction",
                    non_retryable=True,
                )

            sequence += 1
            await self._project(
                input.run_id, sequence, RunState.BUDGET_EXHAUSTED
            )
            raise ApplicationError(
                "decision budget exhausted",
                type="DecisionBudgetExhausted",
                non_retryable=True,
            )
        except ActivityError as error:
            sequence += 1
            cause = error.cause
            if (
                isinstance(cause, ApplicationError)
                and cause.type == "ModelBudgetExceeded"
            ):
                await self._project(
                    input.run_id,
                    sequence,
                    RunState.BUDGET_EXHAUSTED,
                )
                raise
            failure_kind = (
                FailureKind.PERMANENT
                if isinstance(cause, ApplicationError) and cause.non_retryable
                else FailureKind.RETRYABLE
            )
            await self._project(
                input.run_id,
                sequence,
                RunState.FAILED,
                failure_kind=failure_kind,
            )
            raise

    async def _wait_for_delivery_gate(
        self, run_id, expires_at, step_ordinal: int
    ) -> DeliveryGateResult:
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
            gate = await self._resolve_delivery_gate(
                run_id,
                step_ordinal,
                self._approval_id,
                self._action_hash,
            )
            if (
                gate.cancellation_requested
                or gate.approval.state is not ApprovalState.PENDING
            ):
                return gate
            self._approval_signal_received = False
            self._cancellation_signal_received = False

    @staticmethod
    async def _resolve_delivery_gate(
        run_id, step_ordinal: int, approval_id, action_hash
    ) -> DeliveryGateResult:
        return await workflow.execute_activity(
            "resolve_delivery_gate",
            ResolveDeliveryGateInput(
                run_id=run_id,
                step_ordinal=step_ordinal,
                approval_id=approval_id,
                action_hash=action_hash,
            ),
            result_type=DeliveryGateResult,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=ACTIVITY_RETRY,
        )

    @staticmethod
    async def _terminate(
        run_id,
        sequence: int,
        state: RunState,
        failure_kind: FailureKind,
        message: str,
        error_type: str,
    ) -> None:
        await SupportReportWorkflow._project(
            run_id, sequence, state, failure_kind=failure_kind
        )
        raise ApplicationError(message, type=error_type, non_retryable=True)

    @staticmethod
    async def _reject_action(run_id, sequence: int, message: str) -> None:
        await SupportReportWorkflow._project(
            run_id,
            sequence,
            RunState.FAILED,
            failure_kind=FailureKind.PERMANENT,
        )
        raise ApplicationError(
            message,
            type="UnauthorizedAgentAction",
            non_retryable=True,
        )

    @staticmethod
    async def _project(
        run_id,
        sequence: int,
        state: RunState,
        *,
        failure_kind: FailureKind | None = None,
        artifact: ArtifactRef | None = None,
    ) -> None:
        await workflow.execute_activity(
            "project_event",
            ProjectionEvent(
                event_id=workflow.uuid4(),
                run_id=run_id,
                sequence=sequence,
                run_state=state,
                failure_kind=failure_kind,
                artifact=artifact,
            ),
            result_type=bool,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=PROJECTION_RETRY,
        )
