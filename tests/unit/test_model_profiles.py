from pathlib import Path

import pytest
from pydantic import ValidationError

import agentforge.domain as domain
from agentforge.config import Settings


def settings_with_profiles(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://local/agentforge",
        "temporal_address": "127.0.0.1:7233",
        "temporal_namespace": "default",
        "temporal_task_queue": "agentforge-support-report-v1",
        "ticket_root": Path("tickets"),
        "artifact_root": Path("artifacts"),
        "max_ticket_bytes": 65_536,
        "max_total_ticket_bytes": 262_144,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_mock_profile_is_exact_and_independent_of_ollama_limits() -> None:
    import agentforge.model_profiles as profiles

    settings = settings_with_profiles(
        model_context_tokens=4_096,
        model_max_output_tokens=1_024,
        model_max_prompt_chars=12_000,
        model_max_report_chars=8_192,
        model_max_repairs=1,
    )

    assert profiles.MOCK_MODEL_PROFILE.model_dump(mode="json") == {
        "profile_id": "mock-v1",
        "provider": "mock",
        "model_name": "deterministic-support-report-v1",
        "model_digest": None,
        "task_queue": "agentforge-support-report-v1",
        "context_tokens": 0,
        "max_output_tokens": 0,
        "max_prompt_chars": 0,
        "max_report_chars": 0,
        "max_repairs": 0,
    }
    assert settings.model_profiles() == (profiles.MOCK_MODEL_PROFILE,)


def test_ollama_profile_uses_validated_identity_and_budgets() -> None:
    settings = settings_with_profiles(
        enabled_model_profiles=("mock-v1", "ollama-qwen3-4b-instruct-q4-v1"),
        ollama_model_digest="b" * 64,
    )

    profiles = settings.model_profiles()

    assert len(profiles) == 2
    assert profiles[1].model_dump(mode="json") == {
        "profile_id": "ollama-qwen3-4b-instruct-q4-v1",
        "provider": "ollama",
        "model_name": "qwen3:4b-instruct-2507-q4_K_M",
        "model_digest": "b" * 64,
        "task_queue": "agentforge-support-report-ollama-qwen3-4b-v1",
        "context_tokens": 8_192,
        "max_output_tokens": 2_048,
        "max_prompt_chars": 24_000,
        "max_report_chars": 16_384,
        "max_repairs": 2,
    }
    assert settings.resolve_model_profile(
        "ollama-qwen3-4b-instruct-q4-v1"
    ) == profiles[1]


def test_resolve_model_profile_rejects_disabled_profile() -> None:
    settings = settings_with_profiles()

    with pytest.raises(ValueError, match="model profile is not enabled"):
        settings.resolve_model_profile("ollama-qwen3-4b-instruct-q4-v1")


def test_public_descriptors_expose_identity_but_not_configuration() -> None:
    settings = settings_with_profiles(
        enabled_model_profiles=("mock-v1", "ollama-qwen3-4b-instruct-q4-v1"),
        ollama_model_digest="c" * 64,
        ollama_base_url="http://127.0.0.1:11434",
    )

    descriptors = settings.model_profile_descriptors()

    assert [descriptor.profile_id for descriptor in descriptors] == [
        "mock-v1",
        "ollama-qwen3-4b-instruct-q4-v1",
    ]
    assert descriptors[1].model_dump(mode="json") == {
        "profile_id": "ollama-qwen3-4b-instruct-q4-v1",
        "display_name": "Local Qwen3 4B",
        "provider": "ollama",
        "model_name": "qwen3:4b-instruct-2507-q4_K_M",
        "model_digest": "c" * 64,
    }
    serialized = str([item.model_dump(mode="json") for item in descriptors])
    assert "11434" not in serialized
    assert "base_url" not in serialized


def test_profile_contract_rejects_wrong_provider_for_known_id() -> None:
    profile_type = getattr(domain, "ModelProfileSnapshot", None)
    assert profile_type is not None

    with pytest.raises(ValidationError):
        profile_type(
            profile_id="mock-v1",
            provider="ollama",
            model_name="deterministic-support-report-v1",
            model_digest=None,
            task_queue="agentforge-support-report-v1",
            context_tokens=0,
            max_output_tokens=0,
            max_prompt_chars=0,
            max_report_chars=0,
            max_repairs=0,
        )
