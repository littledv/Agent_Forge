from pathlib import Path

import pytest
from pydantic import ValidationError

import agentforge.config as config
from agentforge.config import Settings


def documented_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "AGENTFORGE_DATABASE_URL": (
            "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:5432/agentforge"
        ),
        "AGENTFORGE_TEMPORAL_ADDRESS": "127.0.0.1:7233",
        "AGENTFORGE_TEMPORAL_NAMESPACE": "default",
        "AGENTFORGE_TEMPORAL_TASK_QUEUE": "agentforge-support-report-v1",
        "AGENTFORGE_TICKET_ROOT": "samples/tickets",
        "AGENTFORGE_ARTIFACT_ROOT": "artifacts",
        "AGENTFORGE_MAX_TICKET_BYTES": "65536",
        "AGENTFORGE_MAX_TOTAL_TICKET_BYTES": "262144",
    }
    environment.update(overrides)
    return environment


def test_from_env_accepts_documented_values_and_resolves_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env(documented_environment())

    assert settings.database_url.endswith("/agentforge")
    assert settings.temporal_address == "127.0.0.1:7233"
    assert settings.temporal_namespace == "default"
    assert settings.temporal_task_queue == "agentforge-support-report-v1"
    assert settings.ticket_root == (tmp_path / "samples" / "tickets").resolve()
    assert settings.artifact_root == (tmp_path / "artifacts").resolve()
    assert settings.max_decisions == 4
    assert settings.max_ticket_bytes == 65_536
    assert settings.max_total_ticket_bytes == 262_144
    assert settings.inbox_base_url == "http://127.0.0.1:8001"
    assert settings.approval_ttl_seconds == 300
    assert settings.enabled_model_profiles == ("mock-v1",)
    assert settings.worker_model_profile == "mock-v1"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.ollama_model == "qwen3:4b-instruct-2507-q4_K_M"
    assert settings.ollama_model_digest is None
    assert settings.model_context_tokens == 8_192
    assert settings.model_max_output_tokens == 2_048
    assert settings.model_max_prompt_chars == 24_000
    assert settings.model_max_report_chars == 16_384
    assert settings.model_max_repairs == 2
    assert settings.model_timeout_seconds == 150


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AGENTFORGE_MAX_DECISIONS", "0"),
        ("AGENTFORGE_MAX_TICKET_BYTES", "-1"),
        ("AGENTFORGE_MAX_TOTAL_TICKET_BYTES", "0"),
        ("AGENTFORGE_APPROVAL_TTL_SECONDS", "0"),
    ],
)
def test_from_env_rejects_non_positive_limits(name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(documented_environment(**{name: value}))


@pytest.mark.parametrize(
    "name", ["AGENTFORGE_TICKET_ROOT", "AGENTFORGE_ARTIFACT_ROOT"]
)
def test_from_env_rejects_missing_storage_roots(name: str) -> None:
    environment = documented_environment()
    del environment[name]

    with pytest.raises(ValidationError):
        Settings.from_env(environment)


def test_from_env_accepts_bounded_inbox_overrides() -> None:
    settings = Settings.from_env(
        documented_environment(
            AGENTFORGE_INBOX_BASE_URL="http://localhost:8123",
            AGENTFORGE_APPROVAL_TTL_SECONDS="60",
        )
    )

    assert settings.inbox_base_url == "http://localhost:8123"
    assert settings.approval_ttl_seconds == 60


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:8001",
        "http://example.com:8001",
        "http://user:password@127.0.0.1:8001",
        "http://127.0.0.1:8001/deliveries",
        "http://127.0.0.1:8001?token=value",
        "http://127.0.0.1:8001#fragment",
    ],
)
def test_from_env_rejects_non_loopback_or_ambiguous_inbox_url(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(
            documented_environment(AGENTFORGE_INBOX_BASE_URL=value)
        )


def test_inbox_settings_use_isolated_database_configuration() -> None:
    assert hasattr(config, "InboxSettings")

    defaults = config.InboxSettings.from_env({})
    overridden = config.InboxSettings.from_env(
        {"AGENTFORGE_INBOX_DATABASE_URL": "postgresql+psycopg://local/inbox_test"}
    )

    assert defaults.database_url.endswith("/agentforge_inbox")
    assert overridden.database_url.endswith("/inbox_test")


def test_from_env_normalizes_and_deduplicates_enabled_profiles() -> None:
    settings = Settings.from_env(
        documented_environment(
            AGENTFORGE_ENABLED_MODEL_PROFILES=(
                "ollama-qwen3-4b-instruct-q4-v1, mock-v1, "
                "ollama-qwen3-4b-instruct-q4-v1"
            ),
            AGENTFORGE_OLLAMA_MODEL_DIGEST="a" * 64,
        )
    )

    assert settings.enabled_model_profiles == (
        "mock-v1",
        "ollama-qwen3-4b-instruct-q4-v1",
    )


def test_settings_accept_list_and_tuple_profile_inputs() -> None:
    common = {
        "database_url": "postgresql+psycopg://local/agentforge",
        "temporal_address": "127.0.0.1:7233",
        "temporal_namespace": "default",
        "temporal_task_queue": "agentforge-support-report-v1",
        "ticket_root": Path("tickets"),
        "artifact_root": Path("artifacts"),
        "max_ticket_bytes": 1,
        "max_total_ticket_bytes": 1,
    }

    from_list = Settings(
        **common,
        enabled_model_profiles=["mock-v1"],
    )
    from_tuple = Settings(
        **common,
        enabled_model_profiles=("mock-v1",),
    )

    assert from_list.enabled_model_profiles == ("mock-v1",)
    assert from_tuple.enabled_model_profiles == ("mock-v1",)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AGENTFORGE_ENABLED_MODEL_PROFILES", "unknown-v1"),
        ("AGENTFORGE_WORKER_MODEL_PROFILE", "unknown-v1"),
    ],
)
def test_from_env_rejects_unknown_model_profiles(name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(documented_environment(**{name: value}))


def test_from_env_rejects_worker_profile_that_is_not_enabled() -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(
            documented_environment(
                AGENTFORGE_WORKER_MODEL_PROFILE=(
                    "ollama-qwen3-4b-instruct-q4-v1"
                ),
                AGENTFORGE_OLLAMA_MODEL_DIGEST="a" * 64,
            )
        )


def test_from_env_requires_digest_when_ollama_is_enabled() -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(
            documented_environment(
                AGENTFORGE_ENABLED_MODEL_PROFILES=(
                    "mock-v1,ollama-qwen3-4b-instruct-q4-v1"
                )
            )
        )


@pytest.mark.parametrize("value", ["", "x" * 257])
def test_from_env_rejects_invalid_ollama_model_name(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(
            documented_environment(
                AGENTFORGE_ENABLED_MODEL_PROFILES=(
                    "mock-v1,ollama-qwen3-4b-instruct-q4-v1"
                ),
                AGENTFORGE_OLLAMA_MODEL_DIGEST="a" * 64,
                AGENTFORGE_OLLAMA_MODEL=value,
            )
        )


@pytest.mark.parametrize("value", ["", "ABC", "A" * 64, "g" * 64])
def test_from_env_rejects_invalid_ollama_digest(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(
            documented_environment(
                AGENTFORGE_ENABLED_MODEL_PROFILES=(
                    "mock-v1,ollama-qwen3-4b-instruct-q4-v1"
                ),
                AGENTFORGE_OLLAMA_MODEL_DIGEST=value,
            )
        )


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:11434",
        "http://example.com:11434",
        "http://user:password@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:11434?token=value",
        "http://127.0.0.1:11434#fragment",
        "http://127.0.0.1",
    ],
)
def test_from_env_rejects_non_loopback_or_ambiguous_ollama_url(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(documented_environment(AGENTFORGE_OLLAMA_BASE_URL=value))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AGENTFORGE_MODEL_CONTEXT_TOKENS", "0"),
        ("AGENTFORGE_MODEL_CONTEXT_TOKENS", "131073"),
        ("AGENTFORGE_MODEL_MAX_OUTPUT_TOKENS", "0"),
        ("AGENTFORGE_MODEL_MAX_OUTPUT_TOKENS", "32769"),
        ("AGENTFORGE_MODEL_MAX_PROMPT_CHARS", "0"),
        ("AGENTFORGE_MODEL_MAX_PROMPT_CHARS", "1000001"),
        ("AGENTFORGE_MODEL_MAX_REPORT_CHARS", "0"),
        ("AGENTFORGE_MODEL_MAX_REPORT_CHARS", "262145"),
        ("AGENTFORGE_MODEL_MAX_REPAIRS", "-1"),
        ("AGENTFORGE_MODEL_MAX_REPAIRS", "6"),
        ("AGENTFORGE_MODEL_TIMEOUT_SECONDS", "0"),
        ("AGENTFORGE_MODEL_TIMEOUT_SECONDS", "601"),
    ],
)
def test_from_env_rejects_model_limits_outside_safe_bounds(
    name: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(documented_environment(**{name: value}))
