"""Temporal Worker construction and local process entry point."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from agentforge.activities import AgentActivities, DeliveryProvider
from agentforge.config import Settings
from agentforge.database import create_engine_and_sessionmaker
from agentforge.inbox_client import InboxClient
from agentforge.model_runtime import DecisionProvider
from agentforge.mock_model import DeterministicMockModel
from agentforge.multi_agent_workflow import MultiAgentSupportReportWorkflow
from agentforge.ollama_model import (
    OLLAMA_VERSION,
    OllamaClient,
    OllamaDecisionProvider,
)
from agentforge.ollama_roles import OllamaRoleProvider
from agentforge.role_activities import RoleActivities
from agentforge.role_runtime import RoleProvider
from agentforge.storage import ArtifactStorage
from agentforge.workflow import SupportReportWorkflow


async def create_worker(
    settings: Settings,
    *,
    storage: ArtifactStorage | None = None,
    model: DecisionProvider | None = None,
    roles: RoleProvider | None = None,
    inbox_client: DeliveryProvider | None = None,
) -> Worker:
    temporal_client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        data_converter=pydantic_data_converter,
    )
    _, sessions = create_engine_and_sessionmaker(settings)
    storage = storage or ArtifactStorage(
        ticket_root=settings.ticket_root,
        artifact_root=settings.artifact_root,
        max_ticket_bytes=settings.max_ticket_bytes,
        max_total_ticket_bytes=settings.max_total_ticket_bytes,
    )
    model_profile = settings.resolve_model_profile(settings.worker_model_profile)
    if model_profile.provider == "ollama" and model is None:
        if model_profile.model_digest is None:  # pragma: no cover - validated profile
            raise ValueError("Ollama model digest is required")
        ollama_client = OllamaClient(
            base_url=settings.ollama_base_url,
            profile=model_profile,
            read_timeout_seconds=settings.model_timeout_seconds,
        )
        await ollama_client.verify_runtime(
            OLLAMA_VERSION,
            model_profile.model_name,
            model_profile.model_digest,
        )
        model = OllamaDecisionProvider(storage, model_profile, ollama_client)
        roles = roles or OllamaRoleProvider(model_profile, ollama_client)
    if model is None:
        model = DeterministicMockModel(
            storage, max_draft_bytes=storage.max_total_ticket_bytes
        )
    if roles is None:
        roles = (
            model
            if isinstance(model, DeterministicMockModel)
            else DeterministicMockModel(
                storage, max_draft_bytes=storage.max_total_ticket_bytes
            )
        )
    activities = AgentActivities(
        sessions,
        storage,
        model,
        inbox_client or InboxClient(settings.inbox_base_url),
        settings.approval_ttl_seconds,
        model_profile=model_profile,
    )
    role_activities = RoleActivities(
        sessions, storage, roles, model_profile
    )
    return Worker(
        temporal_client,
        task_queue=model_profile.task_queue,
        workflows=[SupportReportWorkflow, MultiAgentSupportReportWorkflow],
        activities=[
            activities.project_event,
            activities.decide_action,
            activities.read_tickets,
            activities.publish_report,
            activities.create_delivery_approval,
            activities.resolve_delivery_gate,
            activities.deliver_report,
            role_activities.research_role,
            role_activities.write_report_role,
            role_activities.review_report_role,
            role_activities.publish_reviewed_report,
            role_activities.check_cancellation,
        ],
        graceful_shutdown_timeout=timedelta(seconds=30),
    )


async def run_forever(settings: Settings) -> None:
    worker = await create_worker(settings)
    await worker.run()


def main() -> None:
    settings = Settings.from_env()
    asyncio.run(run_forever(settings), loop_factory=asyncio.SelectorEventLoop)
