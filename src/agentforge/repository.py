"""Transactional repositories for durable AgentForge application records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentforge.db_models import (
    ApprovalRecord,
    ArtifactRecord,
    AttemptRecord,
    CancellationRequestRecord,
    DeliveryRecord,
    ProjectionEventRecord,
    RunRecord,
    StepRecord,
    WorkflowSignalOutboxRecord,
    WorkflowStartOutboxRecord,
)
from agentforge.domain import (
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalSignal,
    ApprovalSnapshot,
    ApprovalState,
    ArtifactRef,
    AttemptSnapshot,
    CancellationSignal,
    CancellationSnapshot,
    DeliverReportAction,
    DeliveryAuthorization,
    DeliveryReceipt,
    FailureKind,
    ModelIdentity,
    ModelProfileSnapshot,
    ModelResponseUsage,
    AttemptState,
    ProjectionEvent,
    RunDetailSnapshot,
    RunListItem,
    RunSnapshot,
    RunState,
    StepSnapshot,
    StepState,
    SubmitRun,
)
from agentforge.model_profiles import MOCK_MODEL_PROFILE
from agentforge.multi_agent import TaskGraphLimits, WorkflowKind


class SubmissionConflict(Exception):
    """A submission key was reused with a different canonical request."""


class GraphLimitExceeded(Exception):
    """A new run exceeds its immutable workflow graph limits."""


class ApprovalNotFound(Exception):
    """The approval identifier is unknown."""


class ApprovalConflict(Exception):
    """An approval was changed, expired, or already decided differently."""


class CancellationConflict(Exception):
    """Cancellation is not valid for the current run state."""


class DeliveryConflict(Exception):
    """A stable operation ID was reused with different receipt metadata."""


class StaleModelUsageWrite(Exception):
    """A superseded Temporal attempt tried to change model-usage evidence."""


@dataclass(frozen=True)
class SubmissionResult:
    run: RunSnapshot
    created: bool


@dataclass(frozen=True)
class ClaimedStart:
    id: UUID
    run_id: UUID
    workflow_id: str
    payload: dict[str, object]
    attempt_count: int


@dataclass(frozen=True)
class ApprovalDecisionResult:
    approval: ApprovalSnapshot
    created: bool


@dataclass(frozen=True)
class CancellationResult:
    cancellation: CancellationSnapshot
    created: bool


@dataclass(frozen=True)
class DeliveryResult:
    receipt: DeliveryReceipt
    created: bool


@dataclass(frozen=True)
class ClaimedSignal:
    message_id: UUID
    run_id: UUID
    workflow_id: str
    signal_name: str
    payload: dict[str, object]
    attempt_count: int


def _canonical_request(
    request: SubmitRun, model_profile: ModelProfileSnapshot
) -> tuple[dict[str, object], str]:
    if request.model_profile_id != model_profile.profile_id:
        raise ValueError("request model profile does not match trusted snapshot")
    payload = request.model_dump(mode="json", exclude_defaults=True)
    canonical = {
        "request": payload,
        "model_profile": model_profile.model_dump(mode="json"),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return payload, hashlib.sha256(encoded).hexdigest()


def _artifact_ref(record: ArtifactRecord | None) -> ArtifactRef | None:
    if record is None:
        return None
    return ArtifactRef(
        path=record.path,
        sha256=record.sha256,
        size_bytes=record.size_bytes,
        media_type=record.media_type,
    )


def _run_snapshot(record: RunRecord, artifact: ArtifactRecord | None = None) -> RunSnapshot:
    return RunSnapshot(
        run_id=record.id,
        workflow_id=record.workflow_id,
        state=RunState(record.state),
        created_at=record.created_at,
        updated_at=record.updated_at,
        failure_kind=FailureKind(record.failure_kind) if record.failure_kind else None,
        artifact=_artifact_ref(artifact),
    )


def _approval_snapshot(record: ApprovalRecord) -> ApprovalSnapshot:
    return ApprovalSnapshot(
        approval_id=record.id,
        run_id=record.run_id,
        operation_id=record.operation_id,
        action_hash=record.action_hash,
        action=DeliverReportAction.model_validate(record.action_payload),
        state=ApprovalState(record.state),
        expires_at=record.expires_at,
        created_at=record.created_at,
        decided_at=record.decided_at,
    )


def _cancellation_snapshot(record: CancellationRequestRecord) -> CancellationSnapshot:
    return CancellationSnapshot(
        request_id=record.id,
        run_id=record.run_id,
        created_at=record.created_at,
    )


def _delivery_receipt(record: DeliveryRecord) -> DeliveryReceipt:
    return DeliveryReceipt(
        delivery_id=record.receiver_delivery_id,
        operation_id=record.operation_id,
        artifact_sha256=record.artifact_sha256,
        committed_at=record.committed_at,
    )


class RunRepository:
    @staticmethod
    async def submit(
        session: AsyncSession,
        request: SubmitRun,
        agent_version_id: UUID,
        model_profile: ModelProfileSnapshot = MOCK_MODEL_PROFILE,
        *,
        workflow_kind: WorkflowKind = "support-report-v1",
        graph_limits: TaskGraphLimits | None = None,
    ) -> SubmissionResult:
        payload, request_hash = _canonical_request(request, model_profile)
        profile_payload = model_profile.model_dump(mode="json")

        async with session.begin():
            existing = await session.scalar(
                select(RunRecord).where(
                    RunRecord.submission_key == request.submission_key
                )
            )
            if existing is not None:
                if (
                    existing.request_hash != request_hash
                    or existing.request_payload != payload
                    or existing.model_profile != profile_payload
                ):
                    raise SubmissionConflict(request.submission_key)
                snapshot = _run_snapshot(existing)
        if existing is not None:
            return SubmissionResult(run=snapshot, created=False)

        if workflow_kind == "support-report-v1":
            if graph_limits is not None:
                raise ValueError("version-1 workflow must not include graph limits")
        elif workflow_kind == "support-report-v2":
            if graph_limits is None:
                raise ValueError("version-2 workflow requires graph limits")
            if len(request.ticket_files) > graph_limits.max_ticket_files:
                raise GraphLimitExceeded(
                    f"support report accepts at most {graph_limits.max_ticket_files} tickets"
                )
        else:  # pragma: no cover - protected by the public type contract
            raise ValueError("unsupported workflow kind")

        run_id = uuid4()
        workflow_id = f"agentforge/run/{run_id}"
        start_payload: dict[str, object] = {
            "run_id": str(run_id),
            "goal": request.goal,
            "ticket_files": list(request.ticket_files),
            "model_profile": profile_payload,
        }
        if workflow_kind == "support-report-v2":
            start_payload.update(
                {
                    "workflow_kind": workflow_kind,
                    "graph_limits": graph_limits.model_dump(mode="json"),
                }
            )

        try:
            async with session.begin():
                run = RunRecord(
                    id=run_id,
                    agent_version_id=agent_version_id,
                    submission_key=request.submission_key,
                    request_hash=request_hash,
                    request_payload=payload,
                    model_profile=profile_payload,
                    workflow_id=workflow_id,
                    state=RunState.PENDING_START.value,
                )
                session.add(run)
                session.add(
                    WorkflowStartOutboxRecord(
                        id=uuid4(),
                        run_id=run_id,
                        workflow_id=workflow_id,
                        payload=start_payload,
                    )
                )
                await session.flush()
        except IntegrityError as error:
            await session.rollback()
            async with session.begin():
                existing = await session.scalar(
                    select(RunRecord).where(
                        RunRecord.submission_key == request.submission_key
                    )
                )
                if existing is None:
                    raise error
                if (
                    existing.request_hash != request_hash
                    or existing.request_payload != payload
                    or existing.model_profile != profile_payload
                ):
                    raise SubmissionConflict(request.submission_key) from error
                snapshot = _run_snapshot(existing)
            return SubmissionResult(run=snapshot, created=False)

        snapshot = await RunRepository.get(session, run_id)
        if snapshot is None:  # pragma: no cover - committed row cannot disappear here
            raise RuntimeError(f"Committed run {run_id} could not be read")
        return SubmissionResult(run=snapshot, created=True)

    @staticmethod
    async def get(session: AsyncSession, run_id: UUID) -> RunSnapshot | None:
        run = await session.get(RunRecord, run_id)
        if run is None:
            return None
        artifact = await session.scalar(
            select(ArtifactRecord)
            .where(ArtifactRecord.run_id == run_id)
            .order_by(ArtifactRecord.created_at.desc())
            .limit(1)
        )
        return _run_snapshot(run, artifact)

    @staticmethod
    async def list_recent(
        session: AsyncSession, limit: int
    ) -> tuple[RunListItem, ...]:
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        records = list(
            (
                await session.scalars(
                    select(RunRecord)
                    .order_by(RunRecord.created_at.desc(), RunRecord.id.desc())
                    .limit(limit)
                )
            ).all()
        )
        return tuple(
            RunListItem(
                run_id=record.id,
                goal=SubmitRun.model_validate(record.request_payload).goal,
                state=RunState(record.state),
                failure_kind=(
                    FailureKind(record.failure_kind) if record.failure_kind else None
                ),
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in records
        )

    @staticmethod
    async def get_detail(
        session: AsyncSession, run_id: UUID
    ) -> RunDetailSnapshot | None:
        run = await session.get(RunRecord, run_id)
        if run is None:
            return None
        request = SubmitRun.model_validate(run.request_payload)
        artifact = await session.scalar(
            select(ArtifactRecord)
            .where(ArtifactRecord.run_id == run_id)
            .order_by(ArtifactRecord.created_at.desc(), ArtifactRecord.id.desc())
            .limit(1)
        )
        step_records = list(
            (
                await session.scalars(
                    select(StepRecord)
                    .where(StepRecord.run_id == run_id)
                    .order_by(StepRecord.ordinal)
                )
            ).all()
        )
        attempts_by_step: dict[UUID, list[AttemptSnapshot]] = {
            step.id: [] for step in step_records
        }
        if attempts_by_step:
            attempt_records = list(
                (
                    await session.scalars(
                        select(AttemptRecord)
                        .where(AttemptRecord.step_id.in_(attempts_by_step))
                        .order_by(
                            AttemptRecord.step_id, AttemptRecord.attempt_number
                        )
                    )
                ).all()
            )
            for record in attempt_records:
                attempts_by_step[record.step_id].append(
                    AttemptSnapshot(
                        attempt_number=record.attempt_number,
                        state=AttemptState(record.state),
                        failure_kind=(
                            FailureKind(record.failure_kind)
                            if record.failure_kind
                            else None
                        ),
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                        model_provider=record.model_provider,
                        model_name=record.model_name,
                        model_digest=record.model_digest,
                        model_request_count=record.model_request_count,
                        model_input_tokens=record.model_input_tokens,
                        model_output_tokens=record.model_output_tokens,
                        model_duration_ms=record.model_duration_ms,
                        provider_cost_estimate_usd=(
                            float(record.provider_cost_estimate_usd)
                            if record.provider_cost_estimate_usd is not None
                            else None
                        ),
                        input_tokens_estimated=record.input_tokens_estimated,
                        usage_uncertain=record.usage_uncertain,
                    )
                )
        approval = await session.scalar(
            select(ApprovalRecord)
            .where(ApprovalRecord.run_id == run_id)
            .order_by(ApprovalRecord.created_at.desc(), ApprovalRecord.id.desc())
            .limit(1)
        )
        cancellation_id = await session.scalar(
            select(CancellationRequestRecord.id)
            .where(CancellationRequestRecord.run_id == run_id)
            .limit(1)
        )
        delivery = await session.scalar(
            select(DeliveryRecord)
            .where(DeliveryRecord.run_id == run_id)
            .order_by(DeliveryRecord.committed_at.desc())
            .limit(1)
        )
        return RunDetailSnapshot(
            run=_run_snapshot(run, artifact),
            goal=request.goal,
            ticket_files=request.ticket_files,
            model_profile=ModelProfileSnapshot.model_validate(run.model_profile),
            projection_sequence=run.projection_sequence,
            steps=tuple(
                StepSnapshot(
                    ordinal=record.ordinal,
                    name=record.name,
                    state=StepState(record.state),
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    attempts=tuple(attempts_by_step[record.id]),
                )
                for record in step_records
            ),
            approval=_approval_snapshot(approval) if approval else None,
            cancellation_requested=cancellation_id is not None,
            delivery=_delivery_receipt(delivery) if delivery else None,
        )


class ApprovalRepository:
    @staticmethod
    async def create_pending(
        session: AsyncSession,
        *,
        run_id: UUID,
        authorization: DeliveryAuthorization,
        expires_at: datetime,
    ) -> ApprovalSnapshot:
        async with session.begin():
            await session.execute(
                insert(ApprovalRecord)
                .values(
                    id=authorization.approval_id,
                    run_id=run_id,
                    operation_id=authorization.operation_id,
                    action_hash=authorization.action_hash,
                    action_payload=authorization.action.model_dump(mode="json"),
                    state=ApprovalState.PENDING.value,
                    expires_at=expires_at,
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            record = await session.get(ApprovalRecord, authorization.approval_id)
            if record is None:  # pragma: no cover - insert/read share a transaction
                raise RuntimeError("approval could not be read after insert")
            if (
                record.run_id != run_id
                or record.operation_id != authorization.operation_id
                or record.action_hash != authorization.action_hash
                or record.action_payload
                != authorization.action.model_dump(mode="json")
            ):
                raise ApprovalConflict(str(authorization.approval_id))
        return _approval_snapshot(record)

    @staticmethod
    async def get_for_run(
        session: AsyncSession, run_id: UUID
    ) -> ApprovalSnapshot | None:
        record = await session.scalar(
            select(ApprovalRecord)
            .where(ApprovalRecord.run_id == run_id)
            .order_by(ApprovalRecord.created_at.desc())
            .limit(1)
        )
        return _approval_snapshot(record) if record else None

    @staticmethod
    async def decide(
        session: AsyncSession,
        approval_id: UUID,
        request: ApprovalDecisionRequest,
        *,
        now: datetime | None = None,
    ) -> ApprovalDecisionResult:
        decided_at = now or datetime.now(UTC)
        expired = False
        target_state = (
            ApprovalState.APPROVED
            if request.decision is ApprovalDecision.APPROVE
            else ApprovalState.REJECTED
        )
        async with session.begin():
            record = await session.scalar(
                select(ApprovalRecord)
                .where(ApprovalRecord.id == approval_id)
                .with_for_update()
            )
            if record is None:
                raise ApprovalNotFound(str(approval_id))
            if record.action_hash != request.action_hash:
                raise ApprovalConflict(str(approval_id))
            current_state = ApprovalState(record.state)
            if current_state is ApprovalState.PENDING and record.expires_at <= decided_at:
                record.state = ApprovalState.EXPIRED.value
                record.updated_at = func.now()
                await session.flush()
                expired = True
            elif current_state is target_state:
                return ApprovalDecisionResult(
                    approval=_approval_snapshot(record), created=False
                )
            elif current_state is not ApprovalState.PENDING:
                raise ApprovalConflict(str(approval_id))
            else:
                record.state = target_state.value
                record.decided_at = decided_at
                record.updated_at = func.now()
                run = await session.get(RunRecord, record.run_id)
                if run is None:  # pragma: no cover - protected by foreign key
                    raise RuntimeError("approval run is missing")
                message_id = uuid5(record.id, f"decision:{request.decision.value}")
                signal = ApprovalSignal(
                    message_id=message_id,
                    approval_id=record.id,
                    action_hash=record.action_hash,
                    decision=request.decision,
                )
                session.add(
                    WorkflowSignalOutboxRecord(
                        message_id=message_id,
                        run_id=record.run_id,
                        workflow_id=run.workflow_id,
                        signal_name="approval_decided",
                        payload=signal.model_dump(mode="json"),
                    )
                )
                await session.flush()
                snapshot = _approval_snapshot(record)
        if expired:
            raise ApprovalConflict(str(approval_id))
        return ApprovalDecisionResult(approval=snapshot, created=True)

    @staticmethod
    async def resolve_or_expire(
        session: AsyncSession,
        approval_id: UUID,
        action_hash: str,
        *,
        now: datetime | None = None,
    ) -> ApprovalSnapshot:
        checked_at = now or datetime.now(UTC)
        async with session.begin():
            record = await session.scalar(
                select(ApprovalRecord)
                .where(ApprovalRecord.id == approval_id)
                .with_for_update()
            )
            if record is None:
                raise ApprovalNotFound(str(approval_id))
            if record.action_hash != action_hash:
                raise ApprovalConflict(str(approval_id))
            if (
                ApprovalState(record.state) is ApprovalState.PENDING
                and record.expires_at <= checked_at
            ):
                record.state = ApprovalState.EXPIRED.value
                record.updated_at = func.now()
                await session.flush()
            snapshot = _approval_snapshot(record)
        return snapshot


class CancellationRepository:
    TERMINAL_STATES = {
        RunState.SUCCEEDED.value,
        RunState.FAILED.value,
        RunState.DENIED.value,
        RunState.CANCELLED.value,
        RunState.TIMED_OUT.value,
        RunState.BUDGET_EXHAUSTED.value,
        RunState.NEEDS_ATTENTION.value,
    }

    @staticmethod
    async def request(
        session: AsyncSession,
        run_id: UUID,
        *,
        now: datetime | None = None,
    ) -> CancellationResult:
        requested_at = now or datetime.now(UTC)
        request_id = uuid5(run_id, "cancellation")
        async with session.begin():
            run = await session.scalar(
                select(RunRecord).where(RunRecord.id == run_id).with_for_update()
            )
            if run is None or run.state in CancellationRepository.TERMINAL_STATES:
                raise CancellationConflict(str(run_id))
            existing = await session.get(CancellationRequestRecord, request_id)
            if existing is not None:
                return CancellationResult(
                    cancellation=_cancellation_snapshot(existing), created=False
                )
            record = CancellationRequestRecord(
                id=request_id, run_id=run_id, created_at=requested_at
            )
            session.add(record)
            message_id = uuid5(request_id, "signal")
            signal = CancellationSignal(
                message_id=message_id,
                request_id=request_id,
                run_id=run_id,
            )
            session.add(
                WorkflowSignalOutboxRecord(
                    message_id=message_id,
                    run_id=run_id,
                    workflow_id=run.workflow_id,
                    signal_name="cancellation_requested",
                    payload=signal.model_dump(mode="json"),
                )
            )
            await session.flush()
            snapshot = _cancellation_snapshot(record)
        return CancellationResult(cancellation=snapshot, created=True)

    @staticmethod
    async def exists(session: AsyncSession, run_id: UUID) -> bool:
        request_id = await session.scalar(
            select(CancellationRequestRecord.id).where(
                CancellationRequestRecord.run_id == run_id
            )
        )
        return request_id is not None


class DeliveryRepository:
    @staticmethod
    async def record_receipt(
        session: AsyncSession,
        *,
        run_id: UUID,
        approval_id: UUID,
        receipt: DeliveryReceipt,
    ) -> DeliveryResult:
        async with session.begin():
            approval = await session.scalar(
                select(ApprovalRecord)
                .where(ApprovalRecord.id == approval_id)
                .with_for_update()
            )
            if (
                approval is None
                or approval.run_id != run_id
                or approval.operation_id != receipt.operation_id
                or approval.action_payload["artifact"]["sha256"]
                != receipt.artifact_sha256
            ):
                raise DeliveryConflict(str(receipt.operation_id))
            inserted = await session.scalar(
                insert(DeliveryRecord)
                .values(
                    operation_id=receipt.operation_id,
                    run_id=run_id,
                    approval_id=approval_id,
                    receiver_delivery_id=receipt.delivery_id,
                    artifact_sha256=receipt.artifact_sha256,
                    committed_at=receipt.committed_at,
                )
                .on_conflict_do_nothing(index_elements=["operation_id"])
                .returning(DeliveryRecord.operation_id)
            )
            record = await session.get(DeliveryRecord, receipt.operation_id)
            if record is None:  # pragma: no cover - insert/read share a transaction
                raise RuntimeError("delivery could not be read after insert")
            existing = _delivery_receipt(record)
            if existing != receipt:
                raise DeliveryConflict(str(receipt.operation_id))
        return DeliveryResult(receipt=existing, created=inserted is not None)


class OutboxRepository:
    CLAIM_LEASE = timedelta(minutes=5)

    @staticmethod
    async def claim_batch(
        session: AsyncSession, limit: int, *, now: datetime | None = None
    ) -> list[ClaimedStart]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        claimed_at = now or datetime.now(UTC)
        stale_before = claimed_at - OutboxRepository.CLAIM_LEASE
        statement: Select[tuple[WorkflowStartOutboxRecord]] = (
            select(WorkflowStartOutboxRecord)
            .where(
                WorkflowStartOutboxRecord.started_at.is_(None),
                WorkflowStartOutboxRecord.available_at <= claimed_at,
                or_(
                    WorkflowStartOutboxRecord.claimed_at.is_(None),
                    WorkflowStartOutboxRecord.claimed_at < stale_before,
                ),
            )
            .order_by(WorkflowStartOutboxRecord.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        async with session.begin():
            records = list((await session.scalars(statement)).all())
            for record in records:
                record.claimed_at = claimed_at
        return [
            ClaimedStart(
                id=record.id,
                run_id=record.run_id,
                workflow_id=record.workflow_id,
                payload=record.payload,
                attempt_count=record.attempt_count,
            )
            for record in records
        ]

    @staticmethod
    async def mark_started(
        session: AsyncSession, outbox_id: UUID, *, now: datetime | None = None
    ) -> None:
        started_at = now or datetime.now(UTC)
        async with session.begin():
            await session.execute(
                update(WorkflowStartOutboxRecord)
                .where(WorkflowStartOutboxRecord.id == outbox_id)
                .values(started_at=started_at, claimed_at=None, last_error=None)
            )

    @staticmethod
    async def record_failure(
        session: AsyncSession,
        outbox_id: UUID,
        error: str,
        retry_delay: timedelta,
        *,
        now: datetime | None = None,
    ) -> None:
        failed_at = now or datetime.now(UTC)
        bounded_delay = min(max(retry_delay, timedelta()), timedelta(minutes=5))
        async with session.begin():
            await session.execute(
                update(WorkflowStartOutboxRecord)
                .where(WorkflowStartOutboxRecord.id == outbox_id)
                .values(
                    attempt_count=WorkflowStartOutboxRecord.attempt_count + 1,
                    available_at=failed_at + bounded_delay,
                    claimed_at=None,
                    last_error=error[:2_000],
                )
            )

    @staticmethod
    async def mark_permanent_failure(
        session: AsyncSession,
        outbox_id: UUID,
        run_id: UUID,
        *,
        reason: str = "invalid workflow input",
        now: datetime | None = None,
    ) -> None:
        failed_at = now or datetime.now(UTC)
        async with session.begin():
            await session.execute(
                update(WorkflowStartOutboxRecord)
                .where(WorkflowStartOutboxRecord.id == outbox_id)
                .values(
                    attempt_count=WorkflowStartOutboxRecord.attempt_count + 1,
                    started_at=failed_at,
                    claimed_at=None,
                    last_error=reason[:2_000],
                )
            )
            await session.execute(
                update(RunRecord)
                .where(RunRecord.id == run_id)
                .values(
                    state=RunState.FAILED.value,
                    failure_kind=FailureKind.PERMANENT.value,
                    updated_at=func.now(),
                )
            )


class SignalOutboxRepository:
    CLAIM_LEASE = timedelta(minutes=5)

    @staticmethod
    async def claim_batch(
        session: AsyncSession, limit: int, *, now: datetime | None = None
    ) -> list[ClaimedSignal]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        claimed_at = now or datetime.now(UTC)
        stale_before = claimed_at - SignalOutboxRepository.CLAIM_LEASE
        statement: Select[tuple[WorkflowSignalOutboxRecord]] = (
            select(WorkflowSignalOutboxRecord)
            .where(
                WorkflowSignalOutboxRecord.sent_at.is_(None),
                WorkflowSignalOutboxRecord.available_at <= claimed_at,
                or_(
                    WorkflowSignalOutboxRecord.claimed_at.is_(None),
                    WorkflowSignalOutboxRecord.claimed_at < stale_before,
                ),
            )
            .order_by(WorkflowSignalOutboxRecord.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        async with session.begin():
            records = list((await session.scalars(statement)).all())
            for record in records:
                record.claimed_at = claimed_at
        return [
            ClaimedSignal(
                message_id=record.message_id,
                run_id=record.run_id,
                workflow_id=record.workflow_id,
                signal_name=record.signal_name,
                payload=record.payload,
                attempt_count=record.attempt_count,
            )
            for record in records
        ]

    @staticmethod
    async def mark_sent(
        session: AsyncSession, message_id: UUID, *, now: datetime | None = None
    ) -> None:
        sent_at = now or datetime.now(UTC)
        async with session.begin():
            await session.execute(
                update(WorkflowSignalOutboxRecord)
                .where(WorkflowSignalOutboxRecord.message_id == message_id)
                .values(sent_at=sent_at, claimed_at=None, last_error=None)
            )

    @staticmethod
    async def record_failure(
        session: AsyncSession,
        message_id: UUID,
        error: str,
        retry_delay: timedelta,
        *,
        now: datetime | None = None,
    ) -> None:
        failed_at = now or datetime.now(UTC)
        bounded_delay = min(max(retry_delay, timedelta()), timedelta(minutes=5))
        async with session.begin():
            await session.execute(
                update(WorkflowSignalOutboxRecord)
                .where(WorkflowSignalOutboxRecord.message_id == message_id)
                .values(
                    attempt_count=WorkflowSignalOutboxRecord.attempt_count + 1,
                    available_at=failed_at + bounded_delay,
                    claimed_at=None,
                    last_error=error[:2_000],
                )
            )

    @staticmethod
    async def mark_permanent_failure(
        session: AsyncSession,
        message_id: UUID,
        *,
        reason: str = "invalid workflow signal payload",
        now: datetime | None = None,
    ) -> None:
        failed_at = now or datetime.now(UTC)
        async with session.begin():
            await session.execute(
                update(WorkflowSignalOutboxRecord)
                .where(WorkflowSignalOutboxRecord.message_id == message_id)
                .values(
                    attempt_count=WorkflowSignalOutboxRecord.attempt_count + 1,
                    sent_at=failed_at,
                    claimed_at=None,
                    last_error=reason[:2_000],
                )
            )


class ProjectionRepository:
    @staticmethod
    async def apply(session: AsyncSession, event: ProjectionEvent) -> bool:
        payload = event.model_dump(mode="json", exclude_none=True)
        async with session.begin():
            inserted_event_id = await session.scalar(
                insert(ProjectionEventRecord)
                .values(
                    event_id=event.event_id,
                    run_id=event.run_id,
                    sequence=event.sequence,
                    payload=payload,
                )
                .on_conflict_do_nothing(index_elements=["event_id"])
                .returning(ProjectionEventRecord.event_id)
            )
            if inserted_event_id is None:
                return False

            updated_run_id = await session.scalar(
                update(RunRecord)
                .where(
                    RunRecord.id == event.run_id,
                    RunRecord.projection_sequence < event.sequence,
                )
                .values(
                    state=event.run_state.value,
                    failure_kind=(event.failure_kind.value if event.failure_kind else None),
                    projection_sequence=event.sequence,
                    updated_at=func.now(),
                )
                .returning(RunRecord.id)
            )
            if updated_run_id is None:
                return False

            if event.step_id is not None:
                if (
                    event.step_ordinal is None
                    or event.step_name is None
                    or event.step_state is None
                ):
                    raise ValueError("step projection fields must be supplied together")
                await session.execute(
                    insert(StepRecord)
                    .values(
                        id=event.step_id,
                        run_id=event.run_id,
                        ordinal=event.step_ordinal,
                        name=event.step_name,
                        state=event.step_state.value,
                    )
                    .on_conflict_do_update(
                        constraint="uq_steps_run_ordinal",
                        set_={
                            "state": event.step_state.value,
                            "updated_at": func.now(),
                        },
                    )
                )
            if event.attempt_id is not None:
                if (
                    event.step_id is None
                    or event.attempt_number is None
                    or event.attempt_state is None
                ):
                    raise ValueError("attempt projection fields must be supplied together")
                await session.execute(
                    insert(AttemptRecord)
                    .values(
                        id=event.attempt_id,
                        step_id=event.step_id,
                        attempt_number=event.attempt_number,
                        state=event.attempt_state.value,
                        failure_kind=(
                            event.failure_kind.value if event.failure_kind else None
                        ),
                    )
                    .on_conflict_do_update(
                        constraint="uq_attempts_step_number",
                        set_={
                            "state": event.attempt_state.value,
                            "failure_kind": (
                                event.failure_kind.value if event.failure_kind else None
                            ),
                            "updated_at": func.now(),
                        },
                    )
                )

            if event.artifact is not None:
                await session.execute(
                    insert(ArtifactRecord)
                    .values(
                        id=uuid4(),
                        run_id=event.run_id,
                        path=event.artifact.path,
                        media_type=event.artifact.media_type,
                        sha256=event.artifact.sha256,
                        size_bytes=event.artifact.size_bytes,
                    )
                    .on_conflict_do_nothing(index_elements=["run_id", "path"])
                )
            return True


def step_id_for(run_id: UUID, ordinal: int) -> UUID:
    return uuid5(run_id, f"step:{ordinal}")


def attempt_id_for(step_id: UUID, attempt_number: int) -> UUID:
    return uuid5(step_id, f"attempt:{attempt_number}")


class ModelUsageRepository:
    ALLOWED_STEP_NAMES = frozenset(
        {
            "decide_action",
            *(f"research_shard_{index}" for index in range(1, 5)),
            *(f"write_report_r{revision}" for revision in range(3)),
            *(f"review_report_r{review_round}" for review_round in range(3)),
        }
    )

    @staticmethod
    async def _locked_attempt(
        session: AsyncSession,
        *,
        run_id: UUID,
        ordinal: int,
        attempt_number: int,
    ) -> AttemptRecord:
        step_id = step_id_for(run_id, ordinal)
        locked_step = await session.scalar(
            select(StepRecord)
            .where(StepRecord.id == step_id)
            .with_for_update()
        )
        if locked_step is None:
            raise ValueError("model usage step does not exist")
        if locked_step.name not in ModelUsageRepository.ALLOWED_STEP_NAMES:
            raise ValueError("model usage is limited to an approved model role step")
        higher_attempt = await session.scalar(
            select(AttemptRecord.id)
            .where(
                AttemptRecord.step_id == step_id,
                AttemptRecord.attempt_number > attempt_number,
            )
            .limit(1)
        )
        if higher_attempt is not None:
            raise StaleModelUsageWrite("model usage attempt was superseded")
        attempt = await session.scalar(
            select(AttemptRecord)
            .where(
                AttemptRecord.id == attempt_id_for(step_id, attempt_number),
                AttemptRecord.step_id == step_id,
                AttemptRecord.attempt_number == attempt_number,
            )
            .with_for_update()
        )
        if attempt is None:
            raise ValueError("model usage attempt does not exist")
        return attempt

    @staticmethod
    async def request_started(
        session: AsyncSession,
        *,
        run_id: UUID,
        ordinal: int,
        attempt_number: int,
        identity: ModelIdentity,
        estimated_input_tokens: int,
    ) -> None:
        if estimated_input_tokens <= 0:
            raise ValueError("estimated input tokens must be positive")
        async with session.begin():
            attempt = await ModelUsageRepository._locked_attempt(
                session,
                run_id=run_id,
                ordinal=ordinal,
                attempt_number=attempt_number,
            )
            existing_identity = (
                attempt.model_provider,
                attempt.model_name,
                attempt.model_digest,
            )
            requested_identity = (
                identity.provider,
                identity.model_name,
                identity.model_digest,
            )
            if existing_identity == (None, None, None):
                attempt.model_provider = identity.provider
                attempt.model_name = identity.model_name
                attempt.model_digest = identity.model_digest
            elif existing_identity != requested_identity:
                raise ValueError("model identity mismatch")
            attempt.model_request_count = (attempt.model_request_count or 0) + 1
            if attempt.model_exact_input_count is None:
                attempt.model_exact_input_count = 0
            attempt.model_input_tokens = (
                (attempt.model_input_tokens or 0) + estimated_input_tokens
            )
            attempt.provider_cost_estimate_usd = Decimal("0.000000")
            attempt.input_tokens_estimated = True
            attempt.usage_uncertain = True
            attempt.updated_at = func.now()

    @staticmethod
    async def response_completed(
        session: AsyncSession,
        *,
        run_id: UUID,
        ordinal: int,
        attempt_number: int,
        estimated_input_tokens: int,
        exact_usage: ModelResponseUsage,
    ) -> None:
        if estimated_input_tokens <= 0:
            raise ValueError("estimated input tokens must be positive")
        async with session.begin():
            attempt = await ModelUsageRepository._locked_attempt(
                session,
                run_id=run_id,
                ordinal=ordinal,
                attempt_number=attempt_number,
            )
            if (
                attempt.model_request_count is None
                or attempt.model_request_count <= 0
                or attempt.model_input_tokens is None
                or attempt.model_input_tokens < estimated_input_tokens
            ):
                raise ValueError("model response has no matching request")

            if exact_usage.input_tokens is not None:
                attempt.model_input_tokens = (
                    attempt.model_input_tokens
                    - estimated_input_tokens
                    + exact_usage.input_tokens
                )
                attempt.model_exact_input_count = (
                    attempt.model_exact_input_count or 0
                ) + 1

            attempt.input_tokens_estimated = (
                attempt.model_exact_input_count or 0
            ) < attempt.model_request_count

            if exact_usage.output_tokens is None:
                attempt.model_output_tokens = None
            elif attempt.model_output_tokens is None:
                if attempt.model_request_count == 1:
                    attempt.model_output_tokens = exact_usage.output_tokens
            else:
                attempt.model_output_tokens += exact_usage.output_tokens

            if exact_usage.duration_ms is None:
                attempt.model_duration_ms = None
            elif attempt.model_duration_ms is None:
                if attempt.model_request_count == 1:
                    attempt.model_duration_ms = exact_usage.duration_ms
            else:
                attempt.model_duration_ms += exact_usage.duration_ms

            attempt.usage_uncertain = (
                attempt.input_tokens_estimated
                or attempt.model_output_tokens is None
                or attempt.model_duration_ms is None
            )
            attempt.updated_at = func.now()


async def record_activity_attempt(
    session: AsyncSession,
    *,
    run_id: UUID,
    ordinal: int,
    name: str,
    attempt_number: int,
    attempt_state: AttemptState,
    step_state: StepState,
    failure_kind: FailureKind | None = None,
) -> tuple[UUID, UUID]:
    step_id = step_id_for(run_id, ordinal)
    attempt_id = attempt_id_for(step_id, attempt_number)
    async with session.begin():
        await session.execute(
            insert(StepRecord)
            .values(
                id=step_id,
                run_id=run_id,
                ordinal=ordinal,
                name=name,
                state=step_state.value,
            )
            .on_conflict_do_nothing(constraint="uq_steps_run_ordinal")
        )
        await session.scalar(
            select(StepRecord.id)
            .where(StepRecord.run_id == run_id, StepRecord.ordinal == ordinal)
            .with_for_update()
        )
        higher_attempt_id = await session.scalar(
            select(AttemptRecord.id)
            .where(
                AttemptRecord.step_id == step_id,
                AttemptRecord.attempt_number > attempt_number,
            )
            .limit(1)
        )
        if higher_attempt_id is not None:
            return step_id, attempt_id

        await session.execute(
            update(StepRecord)
            .where(StepRecord.id == step_id)
            .values(state=step_state.value, updated_at=func.now())
        )
        if attempt_state is AttemptState.RUNNING:
            await session.execute(
                update(AttemptRecord)
                .where(
                    AttemptRecord.step_id == step_id,
                    AttemptRecord.attempt_number < attempt_number,
                    AttemptRecord.state == AttemptState.RUNNING.value,
                )
                .values(
                    state=AttemptState.FAILED.value,
                    failure_kind=FailureKind.RETRYABLE.value,
                    updated_at=func.now(),
                )
            )
        await session.execute(
            insert(AttemptRecord)
            .values(
                id=attempt_id,
                step_id=step_id,
                attempt_number=attempt_number,
                state=attempt_state.value,
                failure_kind=failure_kind.value if failure_kind else None,
            )
            .on_conflict_do_update(
                constraint="uq_attempts_step_number",
                set_={
                    "state": attempt_state.value,
                    "failure_kind": failure_kind.value if failure_kind else None,
                    "updated_at": func.now(),
                },
            )
        )
    return step_id, attempt_id
