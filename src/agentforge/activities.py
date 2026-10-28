"""Side-effecting Activities for the support-report Workflow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from pydantic import ConfigDict, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agentforge.domain import (
    AgentAction,
    ApprovalSnapshot,
    ArtifactRef,
    AttemptState,
    DeliveryAuthorization,
    DeliveryReceipt,
    DeliveryRequest,
    DraftRef,
    FailureKind,
    ModelDecisionInput,
    ModelIdentity,
    ModelProfileSnapshot,
    ModelResponseUsage,
    ProjectionEvent,
    StepState,
    StrictModel,
    TicketSnapshotRef,
)
from agentforge.inbox_client import (
    InboxClient,
    InboxUncertainError,
)
from agentforge.mock_model import DeterministicMockModel, InvalidModelState
from agentforge.model_profiles import MOCK_MODEL_PROFILE
from agentforge.model_runtime import (
    DecisionProvider,
    NoOpModelUsageReporter,
)
from agentforge.ollama_model import (
    ModelBudgetExceeded,
    ModelConfigurationError,
    ModelOutputInvalid,
    ModelRequestRejected,
    ModelTransportError,
)
from agentforge.repository import (
    ApprovalRepository,
    CancellationRepository,
    DeliveryConflict,
    DeliveryRepository,
    ModelUsageRepository,
    ProjectionRepository,
    record_activity_attempt,
)
from agentforge.storage import ArtifactStorage, StorageViolation


class DeliveryProvider(Protocol):
    def deliver(self, request: DeliveryRequest) -> DeliveryReceipt: ...


class DecideActionInput(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: ModelDecisionInput
    step_ordinal: int


class ReadTicketsInput(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    step_ordinal: int
    ticket_files: tuple[str, ...]


class PublishReportInput(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    step_ordinal: int
    draft: DraftRef


class CreateDeliveryApprovalInput(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    step_ordinal: int
    authorization: DeliveryAuthorization


class ResolveDeliveryGateInput(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    step_ordinal: int
    approval_id: UUID
    action_hash: str


class DeliveryGateResult(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval: ApprovalSnapshot
    cancellation_requested: bool


class DeliverReportInput(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    step_ordinal: int
    authorization: DeliveryAuthorization


class DurableModelUsageReporter:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        run_id: UUID,
        ordinal: int,
        attempt_number: int,
        identity: ModelIdentity,
    ) -> None:
        self._sessions = sessions
        self._run_id = run_id
        self._ordinal = ordinal
        self._attempt_number = attempt_number
        self._identity = identity

    async def request_started(self, *, estimated_input_tokens: int) -> None:
        async with self._sessions() as session:
            await ModelUsageRepository.request_started(
                session,
                run_id=self._run_id,
                ordinal=self._ordinal,
                attempt_number=self._attempt_number,
                identity=self._identity,
                estimated_input_tokens=estimated_input_tokens,
            )

    async def response_completed(
        self,
        *,
        estimated_input_tokens: int,
        input_tokens: int | None,
        output_tokens: int | None,
        duration_ms: int | None,
    ) -> None:
        async with self._sessions() as session:
            await ModelUsageRepository.response_completed(
                session,
                run_id=self._run_id,
                ordinal=self._ordinal,
                attempt_number=self._attempt_number,
                estimated_input_tokens=estimated_input_tokens,
                exact_usage=ModelResponseUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    duration_ms=duration_ms,
                ),
            )


class AgentActivities:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: ArtifactStorage,
        model: DecisionProvider | None = None,
        inbox_client: DeliveryProvider | None = None,
        approval_ttl_seconds: int = 300,
        model_profile: ModelProfileSnapshot = MOCK_MODEL_PROFILE,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._model = model or DeterministicMockModel(
            storage, max_draft_bytes=storage.max_total_ticket_bytes
        )
        self._inbox_client = inbox_client or InboxClient("http://127.0.0.1:8001")
        self._approval_ttl = timedelta(seconds=approval_ttl_seconds)
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

    @activity.defn(name="project_event")
    async def project_event(self, event: ProjectionEvent) -> bool:
        async with self._sessions() as session:
            return await ProjectionRepository.apply(session, event)

    @activity.defn(name="decide_action")
    async def decide_action(self, input: DecideActionInput) -> AgentAction:
        await self._record(
            run_id=input.decision.run_id,
            ordinal=input.step_ordinal,
            name="decide_action",
            attempt_state=AttemptState.RUNNING,
            step_state=StepState.RUNNING,
        )
        if input.decision.model_profile != self._model_profile:
            await self._record(
                run_id=input.decision.run_id,
                ordinal=input.step_ordinal,
                name="decide_action",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.FAILED,
                failure_kind=FailureKind.PERMANENT,
            )
            raise ApplicationError(
                "model profile mismatch",
                type="ModelProfileMismatch",
                non_retryable=True,
            )
        if self._model_profile.provider == "ollama":
            usage_reporter = DurableModelUsageReporter(
                self._sessions,
                run_id=input.decision.run_id,
                ordinal=input.step_ordinal,
                attempt_number=activity.info().attempt,
                identity=ModelIdentity(
                    provider=self._model_profile.provider,
                    model_name=self._model_profile.model_name,
                    model_digest=self._model_profile.model_digest,
                ),
            )
        else:
            usage_reporter = NoOpModelUsageReporter()
        try:
            result = TypeAdapter(AgentAction).validate_python(
                await self._model.decide(input.decision, usage_reporter)
            )
        except ModelBudgetExceeded:
            await self._record(
                run_id=input.decision.run_id,
                ordinal=input.step_ordinal,
                name="decide_action",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.FAILED,
                failure_kind=FailureKind.PERMANENT,
            )
            raise ApplicationError(
                "model budget exceeded",
                type="ModelBudgetExceeded",
                non_retryable=True,
            ) from None
        except ModelTransportError:
            await self._record(
                run_id=input.decision.run_id,
                ordinal=input.step_ordinal,
                name="decide_action",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.RETRY_SCHEDULED,
                failure_kind=FailureKind.RETRYABLE,
            )
            raise ApplicationError(
                "model provider unavailable",
                type="ModelTransportError",
                non_retryable=False,
            ) from None
        except (
            InvalidModelState,
            StorageViolation,
            ModelConfigurationError,
            ModelRequestRejected,
            ModelOutputInvalid,
            ValueError,
        ) as error:
            await self._record(
                run_id=input.decision.run_id,
                ordinal=input.step_ordinal,
                name="decide_action",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.FAILED,
                failure_kind=FailureKind.PERMANENT,
            )
            raise ApplicationError(
                "model decision rejected", type=type(error).__name__, non_retryable=True
            ) from None
        except Exception as error:
            await self._record(
                run_id=input.decision.run_id,
                ordinal=input.step_ordinal,
                name="decide_action",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.RETRY_SCHEDULED,
                failure_kind=FailureKind.RETRYABLE,
            )
            raise ApplicationError(
                "model decision failed",
                type=type(error).__name__,
                non_retryable=False,
            ) from None
        await self._record(
            run_id=input.decision.run_id,
            ordinal=input.step_ordinal,
            name="decide_action",
            attempt_state=AttemptState.SUCCEEDED,
            step_state=StepState.SUCCEEDED,
        )
        return result

    @activity.defn(name="read_tickets")
    async def read_tickets(self, input: ReadTicketsInput) -> TicketSnapshotRef:
        await self._record(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name="read_tickets",
            attempt_state=AttemptState.RUNNING,
            step_state=StepState.RUNNING,
        )
        try:
            result = self._storage.snapshot_tickets(input.run_id, input.ticket_files)
        except StorageViolation as error:
            await self._record(
                run_id=input.run_id,
                ordinal=input.step_ordinal,
                name="read_tickets",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.FAILED,
                failure_kind=FailureKind.PERMANENT,
            )
            raise ApplicationError(
                "ticket read denied", type=type(error).__name__, non_retryable=True
            ) from None
        except Exception:
            await self._record(
                run_id=input.run_id,
                ordinal=input.step_ordinal,
                name="read_tickets",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.RETRY_SCHEDULED,
                failure_kind=FailureKind.RETRYABLE,
            )
            raise
        await self._record(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name="read_tickets",
            attempt_state=AttemptState.SUCCEEDED,
            step_state=StepState.SUCCEEDED,
        )
        return result

    @activity.defn(name="publish_report")
    async def publish_report(self, input: PublishReportInput) -> ArtifactRef:
        await self._record(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name="publish_report",
            attempt_state=AttemptState.RUNNING,
            step_state=StepState.RUNNING,
        )
        try:
            result = self._storage.publish_report(input.run_id, input.draft)
        except StorageViolation as error:
            await self._record(
                run_id=input.run_id,
                ordinal=input.step_ordinal,
                name="publish_report",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.FAILED,
                failure_kind=FailureKind.PERMANENT,
            )
            raise ApplicationError(
                "report publication denied",
                type=type(error).__name__,
                non_retryable=True,
            ) from None
        except Exception:
            await self._record(
                run_id=input.run_id,
                ordinal=input.step_ordinal,
                name="publish_report",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.RETRY_SCHEDULED,
                failure_kind=FailureKind.RETRYABLE,
            )
            raise
        await self._record(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name="publish_report",
            attempt_state=AttemptState.SUCCEEDED,
            step_state=StepState.SUCCEEDED,
        )
        return result
    @activity.defn(name="create_delivery_approval")
    async def create_delivery_approval(
        self, input: CreateDeliveryApprovalInput
    ) -> ApprovalSnapshot:
        await self._record(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name="create_delivery_approval",
            attempt_state=AttemptState.RUNNING,
            step_state=StepState.RUNNING,
        )
        try:
            async with self._sessions() as session:
                result = await ApprovalRepository.create_pending(
                    session,
                    run_id=input.run_id,
                    authorization=input.authorization,
                    expires_at=datetime.now(UTC) + self._approval_ttl,
                )
        except Exception:
            await self._record(
                run_id=input.run_id,
                ordinal=input.step_ordinal,
                name="create_delivery_approval",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.RETRY_SCHEDULED,
                failure_kind=FailureKind.RETRYABLE,
            )
            raise
        await self._record(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name="create_delivery_approval",
            attempt_state=AttemptState.SUCCEEDED,
            step_state=StepState.SUCCEEDED,
        )
        return result

    @activity.defn(name="resolve_delivery_gate")
    async def resolve_delivery_gate(
        self, input: ResolveDeliveryGateInput
    ) -> DeliveryGateResult:
        await self._record(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name="resolve_delivery_gate",
            attempt_state=AttemptState.RUNNING,
            step_state=StepState.RUNNING,
        )
        try:
            async with self._sessions() as session:
                approval = await ApprovalRepository.resolve_or_expire(
                    session, input.approval_id, input.action_hash
                )
            async with self._sessions() as session:
                cancellation_requested = await CancellationRepository.exists(
                    session, input.run_id
                )
        except Exception:
            await self._record(
                run_id=input.run_id,
                ordinal=input.step_ordinal,
                name="resolve_delivery_gate",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.RETRY_SCHEDULED,
                failure_kind=FailureKind.RETRYABLE,
            )
            raise
        await self._record(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name="resolve_delivery_gate",
            attempt_state=AttemptState.SUCCEEDED,
            step_state=StepState.SUCCEEDED,
        )
        return DeliveryGateResult(
            approval=approval, cancellation_requested=cancellation_requested
        )

    @activity.defn(name="deliver_report")
    async def deliver_report(self, input: DeliverReportInput) -> DeliveryReceipt:
        await self._record(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name="deliver_report",
            attempt_state=AttemptState.RUNNING,
            step_state=StepState.RUNNING,
        )
        attempt = activity.info().attempt
        try:
            content = self._storage.read_verified_artifact(
                input.authorization.action.artifact
            ).decode("utf-8")
            request = DeliveryRequest(
                operation_id=input.authorization.operation_id,
                action_hash=input.authorization.action_hash,
                artifact=input.authorization.action.artifact,
                content=content,
            )
            receipt = await asyncio.to_thread(self._inbox_client.deliver, request)
            async with self._sessions() as session:
                stored = await DeliveryRepository.record_receipt(
                    session,
                    run_id=input.run_id,
                    approval_id=input.authorization.approval_id,
                    receipt=receipt,
                )
            result = stored.receipt
        except InboxUncertainError:
            await self._record(
                run_id=input.run_id,
                ordinal=input.step_ordinal,
                name="deliver_report",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.UNCERTAIN,
                failure_kind=FailureKind.UNCERTAIN,
            )
            raise ApplicationError(
                "delivery result is uncertain",
                type="DeliveryUncertain",
                non_retryable=True,
            ) from None
        except (StorageViolation, UnicodeDecodeError, ValidationError) as error:
            await self._record(
                run_id=input.run_id,
                ordinal=input.step_ordinal,
                name="deliver_report",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.FAILED,
                failure_kind=FailureKind.PERMANENT,
            )
            raise ApplicationError(
                "report delivery denied",
                type=type(error).__name__,
                non_retryable=True,
            ) from None
        except DeliveryConflict:
            await self._record(
                run_id=input.run_id,
                ordinal=input.step_ordinal,
                name="deliver_report",
                attempt_state=AttemptState.FAILED,
                step_state=StepState.UNCERTAIN,
                failure_kind=FailureKind.UNCERTAIN,
            )
            raise ApplicationError(
                "delivery receipt conflicts with stored state",
                type="DeliveryUncertain",
                non_retryable=True,
            ) from None
        except Exception:
            exhausted = attempt >= 3
            await self._record(
                run_id=input.run_id,
                ordinal=input.step_ordinal,
                name="deliver_report",
                attempt_state=AttemptState.FAILED,
                step_state=(
                    StepState.UNCERTAIN if exhausted else StepState.RETRY_SCHEDULED
                ),
                failure_kind=(
                    FailureKind.UNCERTAIN if exhausted else FailureKind.RETRYABLE
                ),
            )
            raise
        await self._record(
            run_id=input.run_id,
            ordinal=input.step_ordinal,
            name="deliver_report",
            attempt_state=AttemptState.SUCCEEDED,
            step_state=StepState.SUCCEEDED,
        )
        return result
