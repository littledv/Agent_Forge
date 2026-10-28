"""Versioned model-profile definitions with no runtime side effects."""

from __future__ import annotations

from agentforge.domain import (
    ModelProfileDescriptor,
    ModelProfileSnapshot,
    default_mock_model_profile,
)


MOCK_PROFILE_ID = "mock-v1"
OLLAMA_PROFILE_ID = "ollama-qwen3-4b-instruct-q4-v1"
MOCK_MODEL_NAME = "deterministic-support-report-v1"
OLLAMA_TASK_QUEUE = "agentforge-support-report-ollama-qwen3-4b-v1"

MOCK_MODEL_PROFILE = default_mock_model_profile()


def build_ollama_profile(
    *,
    model_name: str,
    model_digest: str,
    context_tokens: int,
    max_output_tokens: int,
    max_prompt_chars: int,
    max_report_chars: int,
    max_repairs: int,
) -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        profile_id=OLLAMA_PROFILE_ID,
        provider="ollama",
        model_name=model_name,
        model_digest=model_digest,
        task_queue=OLLAMA_TASK_QUEUE,
        context_tokens=context_tokens,
        max_output_tokens=max_output_tokens,
        max_prompt_chars=max_prompt_chars,
        max_report_chars=max_report_chars,
        max_repairs=max_repairs,
    )


def public_descriptor(profile: ModelProfileSnapshot) -> ModelProfileDescriptor:
    display_names = {
        MOCK_PROFILE_ID: "Deterministic mock",
        OLLAMA_PROFILE_ID: "Local Qwen3 4B",
    }
    return ModelProfileDescriptor(
        profile_id=profile.profile_id,
        display_name=display_names[profile.profile_id],
        provider=profile.provider,
        model_name=profile.model_name,
        model_digest=profile.model_digest,
    )
