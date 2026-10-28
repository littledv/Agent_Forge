"""Validated AgentForge runtime configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from agentforge.domain import (
    ModelProfileDescriptor,
    ModelProfileId,
    ModelProfileSnapshot,
)
from agentforge.model_profiles import (
    MOCK_MODEL_PROFILE,
    MOCK_PROFILE_ID,
    OLLAMA_PROFILE_ID,
    build_ollama_profile,
    public_descriptor,
)


ApprovalTtlSeconds = Annotated[int, Field(gt=0, le=86_400)]
ModelContextTokens = Annotated[int, Field(gt=0, le=131_072)]
ModelOutputTokens = Annotated[int, Field(gt=0, le=32_768)]
ModelPromptChars = Annotated[int, Field(gt=0, le=1_000_000)]
ModelReportChars = Annotated[int, Field(gt=0, le=262_144)]
ModelRepairs = Annotated[int, Field(ge=0, le=5)]
ModelTimeoutSeconds = Annotated[int, Field(gt=0, le=600)]
OllamaDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
OllamaModelName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]


def _validate_loopback_http_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base URL must be an unambiguous loopback HTTP URL")
    try:
        if parsed.port is None:
            raise ValueError("base URL must include a port")
    except ValueError as error:
        raise ValueError("base URL must include a valid port") from error
    return value.rstrip("/")


def _normalize_enabled_profiles(value: object) -> tuple[object, ...]:
    if isinstance(value, str):
        candidates: tuple[object, ...] = tuple(
            item.strip() for item in value.split(",") if item.strip()
        )
    elif isinstance(value, (list, tuple)):
        candidates = tuple(value)
    else:
        raise ValueError("enabled model profiles must be a list, tuple, or CSV")

    ordered: list[object] = [MOCK_PROFILE_ID]
    for candidate in candidates:
        if candidate not in ordered:
            ordered.append(candidate)
    return tuple(ordered)


class Settings(BaseModel):
    """Environment-backed settings shared by AgentForge processes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    database_url: str
    temporal_address: str
    temporal_namespace: str
    temporal_task_queue: str
    ticket_root: Path
    artifact_root: Path
    max_decisions: PositiveInt = 4
    max_ticket_bytes: PositiveInt
    max_total_ticket_bytes: PositiveInt
    inbox_base_url: str = "http://127.0.0.1:8001"
    approval_ttl_seconds: ApprovalTtlSeconds = 300
    enabled_model_profiles: tuple[ModelProfileId, ...] = (MOCK_PROFILE_ID,)
    worker_model_profile: ModelProfileId = MOCK_PROFILE_ID
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: OllamaModelName = "qwen3:4b-instruct-2507-q4_K_M"
    ollama_model_digest: OllamaDigest | None = None
    model_context_tokens: ModelContextTokens = 8_192
    model_max_output_tokens: ModelOutputTokens = 2_048
    model_max_prompt_chars: ModelPromptChars = 24_000
    model_max_report_chars: ModelReportChars = 16_384
    model_max_repairs: ModelRepairs = 2
    model_timeout_seconds: ModelTimeoutSeconds = 150

    _inbox_url_is_loopback = field_validator("inbox_base_url")(
        _validate_loopback_http_url
    )
    _ollama_url_is_loopback = field_validator("ollama_base_url")(
        _validate_loopback_http_url
    )
    _enabled_profiles_are_normalized = field_validator(
        "enabled_model_profiles", mode="before"
    )(_normalize_enabled_profiles)

    @model_validator(mode="after")
    def validate_model_configuration(self) -> Settings:
        if self.worker_model_profile not in self.enabled_model_profiles:
            raise ValueError("Worker model profile must be enabled")
        if (
            OLLAMA_PROFILE_ID in self.enabled_model_profiles
            and self.ollama_model_digest is None
        ):
            raise ValueError("Ollama model digest is required when Ollama is enabled")
        return self

    def model_profiles(self) -> tuple[ModelProfileSnapshot, ...]:
        profiles = [MOCK_MODEL_PROFILE]
        if OLLAMA_PROFILE_ID in self.enabled_model_profiles:
            if self.ollama_model_digest is None:  # pragma: no cover - model validator
                raise ValueError("Ollama model digest is required")
            profiles.append(
                build_ollama_profile(
                    model_name=self.ollama_model,
                    model_digest=self.ollama_model_digest,
                    context_tokens=self.model_context_tokens,
                    max_output_tokens=self.model_max_output_tokens,
                    max_prompt_chars=self.model_max_prompt_chars,
                    max_report_chars=self.model_max_report_chars,
                    max_repairs=self.model_max_repairs,
                )
            )
        return tuple(profiles)

    def resolve_model_profile(
        self, profile_id: ModelProfileId
    ) -> ModelProfileSnapshot:
        for profile in self.model_profiles():
            if profile.profile_id == profile_id:
                return profile
        raise ValueError("model profile is not enabled")

    def model_profile_descriptors(self) -> tuple[ModelProfileDescriptor, ...]:
        return tuple(public_descriptor(profile) for profile in self.model_profiles())

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from an injected mapping or the process environment."""

        source = os.environ if env is None else env

        def resolved_path(name: str) -> Path | None:
            value = source.get(name)
            return Path(value).resolve() if value else None

        return cls(
            database_url=source.get("AGENTFORGE_DATABASE_URL"),
            temporal_address=source.get("AGENTFORGE_TEMPORAL_ADDRESS"),
            temporal_namespace=source.get("AGENTFORGE_TEMPORAL_NAMESPACE"),
            temporal_task_queue=source.get("AGENTFORGE_TEMPORAL_TASK_QUEUE"),
            ticket_root=resolved_path("AGENTFORGE_TICKET_ROOT"),
            artifact_root=resolved_path("AGENTFORGE_ARTIFACT_ROOT"),
            max_decisions=source.get("AGENTFORGE_MAX_DECISIONS", "4"),
            max_ticket_bytes=source.get("AGENTFORGE_MAX_TICKET_BYTES"),
            max_total_ticket_bytes=source.get("AGENTFORGE_MAX_TOTAL_TICKET_BYTES"),
            inbox_base_url=source.get(
                "AGENTFORGE_INBOX_BASE_URL", "http://127.0.0.1:8001"
            ),
            approval_ttl_seconds=source.get(
                "AGENTFORGE_APPROVAL_TTL_SECONDS", "300"
            ),
            enabled_model_profiles=source.get(
                "AGENTFORGE_ENABLED_MODEL_PROFILES", MOCK_PROFILE_ID
            ),
            worker_model_profile=source.get(
                "AGENTFORGE_WORKER_MODEL_PROFILE", MOCK_PROFILE_ID
            ),
            ollama_base_url=source.get(
                "AGENTFORGE_OLLAMA_BASE_URL", "http://127.0.0.1:11434"
            ),
            ollama_model=source.get(
                "AGENTFORGE_OLLAMA_MODEL", "qwen3:4b-instruct-2507-q4_K_M"
            ),
            ollama_model_digest=source.get("AGENTFORGE_OLLAMA_MODEL_DIGEST"),
            model_context_tokens=source.get(
                "AGENTFORGE_MODEL_CONTEXT_TOKENS", "8192"
            ),
            model_max_output_tokens=source.get(
                "AGENTFORGE_MODEL_MAX_OUTPUT_TOKENS", "2048"
            ),
            model_max_prompt_chars=source.get(
                "AGENTFORGE_MODEL_MAX_PROMPT_CHARS", "24000"
            ),
            model_max_report_chars=source.get(
                "AGENTFORGE_MODEL_MAX_REPORT_CHARS", "16384"
            ),
            model_max_repairs=source.get("AGENTFORGE_MODEL_MAX_REPAIRS", "2"),
            model_timeout_seconds=source.get(
                "AGENTFORGE_MODEL_TIMEOUT_SECONDS", "150"
            ),
        )


class InboxSettings(BaseModel):
    """Receiver-owned configuration kept separate from AgentForge settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    database_url: Annotated[str, Field(min_length=1)] = (
        "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:5432/"
        "agentforge_inbox"
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> InboxSettings:
        source = os.environ if env is None else env
        return cls(
            database_url=source.get(
                "AGENTFORGE_INBOX_DATABASE_URL",
                "postgresql+psycopg://temporal:temporal-local-only@127.0.0.1:5432/"
                "agentforge_inbox",
            )
        )
