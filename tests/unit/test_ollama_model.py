from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from agentforge.domain import (
    ArtifactRef,
    DeliverReportAction,
    DeliveryReceipt,
    DraftRef,
    FinishAction,
    ModelDecisionInput,
    ModelProfileSnapshot,
    PublishReportAction,
    ReadTicketsAction,
)
from agentforge.model_profiles import build_ollama_profile
from agentforge.ollama_model import (
    GeneratedReport,
    ModelBudgetExceeded,
    ModelConfigurationError,
    ModelOutputInvalid,
    ModelRequestRejected,
    ModelTransportError,
    OllamaClient,
    OllamaDecisionProvider,
    ReportGenerationRequest,
    VerifiedTicket,
)
from agentforge.role_runtime import ResearchNoteBody
from agentforge.storage import ArtifactStorage, StorageViolation


RUN_ID = UUID("00000000-0000-0000-0000-000000000020")
GOAL = "Find recurring support themes"


def ollama_profile(**overrides: object) -> ModelProfileSnapshot:
    values: dict[str, object] = {
        "model_name": "qwen3:4b-instruct-2507-q4_K_M",
        "model_digest": "a" * 64,
        "context_tokens": 8_192,
        "max_output_tokens": 2_048,
        "max_prompt_chars": 24_000,
        "max_report_chars": 16_384,
        "max_repairs": 2,
    }
    values.update(overrides)
    return build_ollama_profile(**values)  # type: ignore[arg-type]


def artifact_storage(tmp_path: Path, *, ticket_text: str = "Export is pending.") -> ArtifactStorage:
    tickets = tmp_path / "tickets"
    tickets.mkdir()
    (tickets / "b.txt").write_text("Password reset is confusing.", encoding="utf-8")
    (tickets / "a.txt").write_text(ticket_text, encoding="utf-8")
    return ArtifactStorage(
        ticket_root=tickets,
        artifact_root=tmp_path / "artifacts",
        max_ticket_bytes=65_536,
        max_total_ticket_bytes=262_144,
    )


def decision_input(profile: ModelProfileSnapshot, **overrides: object) -> ModelDecisionInput:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "goal": GOAL,
        "ticket_files": ("b.txt", "a.txt"),
        "decision_number": 1,
        "model_profile": profile,
    }
    values.update(overrides)
    return ModelDecisionInput(**values)


class UnusedUsageReporter:
    async def request_started(self, *, estimated_input_tokens: int) -> None:
        return None

    async def response_completed(
        self,
        *,
        estimated_input_tokens: int,
        input_tokens: int | None,
        output_tokens: int | None,
        duration_ms: int | None,
    ) -> None:
        return None


class RecordingClient:
    def __init__(self, *results: GeneratedReport | Exception) -> None:
        self.results = list(results)
        self.calls: list[ReportGenerationRequest] = []

    async def generate_report(
        self, request: ReportGenerationRequest, usage: object
    ) -> GeneratedReport:
        self.calls.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_controller_generates_from_verified_sorted_tickets_and_writes_draft(
    tmp_path: Path,
) -> None:
    profile = ollama_profile()
    storage = artifact_storage(tmp_path)
    snapshot = storage.snapshot_tickets(RUN_ID, ("b.txt", "a.txt"))
    report = f"# Report\n\n{GOAL}\n\na.txt\nb.txt\n"
    client = RecordingClient(GeneratedReport(report_markdown=report))
    provider = OllamaDecisionProvider(storage, profile, client)  # type: ignore[arg-type]

    action = await provider.decide(
        decision_input(profile, ticket_snapshot=snapshot), UnusedUsageReporter()
    )

    assert isinstance(action, PublishReportAction)
    assert action.ticket_snapshot == snapshot
    assert client.calls[0].goal == GOAL
    assert [ticket.path for ticket in client.calls[0].tickets] == ["a.txt", "b.txt"]
    assert [ticket.content for ticket in client.calls[0].tickets] == [
        "Export is pending.",
        "Password reset is confusing.",
    ]
    assert action.draft.path == f"drafts/{RUN_ID}/support-report.md"
    assert (storage.artifact_root / action.draft.path).read_text(
        encoding="utf-8"
    ) == report
    assert list((storage.artifact_root / ".staging" / str(RUN_ID)).iterdir()) == []


@pytest.mark.asyncio
async def test_controller_owns_all_non_generation_state_transitions(tmp_path: Path) -> None:
    profile = ollama_profile()
    storage = artifact_storage(tmp_path)
    snapshot = storage.snapshot_tickets(RUN_ID, ("b.txt", "a.txt"))
    draft = storage.write_draft(RUN_ID, b"trusted draft")
    artifact = storage.publish_report(RUN_ID, draft)
    receipt = DeliveryReceipt(
        delivery_id=UUID("00000000-0000-0000-0000-000000000021"),
        operation_id=UUID("00000000-0000-0000-0000-000000000022"),
        artifact_sha256=artifact.sha256,
        committed_at=datetime(2026, 10, 25, tzinfo=UTC),
    )
    client = RecordingClient()
    provider = OllamaDecisionProvider(storage, profile, client)  # type: ignore[arg-type]

    read = await provider.decide(decision_input(profile), UnusedUsageReporter())
    publish = await provider.decide(
        decision_input(profile, ticket_snapshot=snapshot, draft=draft),
        UnusedUsageReporter(),
    )
    deliver = await provider.decide(
        decision_input(
            profile, ticket_snapshot=snapshot, draft=draft, artifact=artifact
        ),
        UnusedUsageReporter(),
    )
    finish = await provider.decide(
        decision_input(
            profile,
            ticket_snapshot=snapshot,
            draft=draft,
            artifact=artifact,
            delivery=receipt,
        ),
        UnusedUsageReporter(),
    )

    assert read == ReadTicketsAction(type="read_tickets", ticket_files=("b.txt", "a.txt"))
    assert publish == PublishReportAction(
        type="publish_report", ticket_snapshot=snapshot, draft=draft
    )
    assert deliver == DeliverReportAction(
        type="deliver_report", target="simulated-inbox", artifact=artifact
    )
    assert finish == FinishAction(type="finish", artifact=artifact)
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"draft": DraftRef(path="drafts/x.md", sha256="a" * 64, size_bytes=1)},
        {
            "artifact": ArtifactRef(
                path="reports/x.md",
                sha256="a" * 64,
                size_bytes=1,
                media_type="text/markdown",
            )
        },
        {
            "delivery": DeliveryReceipt(
                delivery_id=UUID("00000000-0000-0000-0000-000000000023"),
                operation_id=UUID("00000000-0000-0000-0000-000000000024"),
                artifact_sha256="a" * 64,
                committed_at=datetime(2026, 10, 25, tzinfo=UTC),
            )
        },
    ],
)
async def test_controller_rejects_inconsistent_state_without_provider_call(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    profile = ollama_profile()
    client = RecordingClient()
    provider = OllamaDecisionProvider(
        artifact_storage(tmp_path), profile, client  # type: ignore[arg-type]
    )

    with pytest.raises(ModelOutputInvalid):
        await provider.decide(
            decision_input(profile, **overrides), UnusedUsageReporter()
        )

    assert client.calls == []


@pytest.mark.asyncio
async def test_controller_rejects_profile_receipt_and_storage_mismatches_without_call(
    tmp_path: Path,
) -> None:
    profile = ollama_profile()
    storage = artifact_storage(tmp_path)
    snapshot = storage.snapshot_tickets(RUN_ID, ("b.txt", "a.txt"))
    draft = storage.write_draft(RUN_ID, b"trusted draft")
    artifact = storage.publish_report(RUN_ID, draft)
    wrong_receipt = DeliveryReceipt(
        delivery_id=UUID("00000000-0000-0000-0000-000000000025"),
        operation_id=UUID("00000000-0000-0000-0000-000000000026"),
        artifact_sha256="b" * 64,
        committed_at=datetime(2026, 10, 25, tzinfo=UTC),
    )
    client = RecordingClient()
    provider = OllamaDecisionProvider(storage, profile, client)  # type: ignore[arg-type]

    with pytest.raises(ModelConfigurationError):
        await provider.decide(
            decision_input(profile.model_copy(update={"max_repairs": 1})),
            UnusedUsageReporter(),
        )
    with pytest.raises(ModelOutputInvalid):
        await provider.decide(
            decision_input(
                profile,
                ticket_snapshot=snapshot,
                draft=draft,
                artifact=artifact,
                delivery=wrong_receipt,
            ),
            UnusedUsageReporter(),
        )
    snapshot_path = storage.artifact_root / snapshot.path
    snapshot_path.write_text("corrupted", encoding="utf-8")
    with pytest.raises(StorageViolation):
        await provider.decide(
            decision_input(profile, ticket_snapshot=snapshot), UnusedUsageReporter()
        )

    assert client.calls == []


@pytest.mark.asyncio
async def test_controller_refuses_oversized_prompt_before_provider_call(tmp_path: Path) -> None:
    profile = ollama_profile(max_prompt_chars=24_000)
    storage = artifact_storage(tmp_path, ticket_text="x" * 24_000)
    snapshot = storage.snapshot_tickets(RUN_ID, ("a.txt",))
    client = RecordingClient()
    provider = OllamaDecisionProvider(storage, profile, client)  # type: ignore[arg-type]

    with pytest.raises(ModelBudgetExceeded):
        await provider.decide(
            decision_input(
                profile, ticket_files=("a.txt",), ticket_snapshot=snapshot
            ),
            UnusedUsageReporter(),
        )

    assert client.calls == []


@pytest.mark.asyncio
async def test_controller_allows_exactly_two_sanitized_repairs(tmp_path: Path) -> None:
    profile = ollama_profile(max_repairs=2)
    storage = artifact_storage(tmp_path)
    snapshot = storage.snapshot_tickets(RUN_ID, ("a.txt", "b.txt"))
    valid = GeneratedReport(report_markdown=f"{GOAL}\na.txt\nb.txt")
    client = RecordingClient(
        ModelOutputInvalid("invalid_schema"),
        ModelOutputInvalid("missing_goal"),
        valid,
    )
    provider = OllamaDecisionProvider(storage, profile, client)  # type: ignore[arg-type]

    action = await provider.decide(
        decision_input(profile, ticket_snapshot=snapshot), UnusedUsageReporter()
    )

    assert isinstance(action, PublishReportAction)
    assert [call.repair_category for call in client.calls] == [
        None,
        "invalid_schema",
        "missing_goal",
    ]


@pytest.mark.asyncio
async def test_controller_stops_after_two_repairs(tmp_path: Path) -> None:
    profile = ollama_profile(max_repairs=2)
    storage = artifact_storage(tmp_path)
    snapshot = storage.snapshot_tickets(RUN_ID, ("a.txt", "b.txt"))
    client = RecordingClient(
        ModelOutputInvalid("invalid_schema"),
        ModelOutputInvalid("invalid_schema"),
        ModelOutputInvalid("invalid_schema"),
    )
    provider = OllamaDecisionProvider(storage, profile, client)  # type: ignore[arg-type]

    with pytest.raises(ModelOutputInvalid) as caught:
        await provider.decide(
            decision_input(profile, ticket_snapshot=snapshot), UnusedUsageReporter()
        )

    assert str(caught.value) == "model output invalid: repair_exhausted"
    assert len(client.calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        ModelTransportError("model provider unavailable"),
        ModelOutputInvalid("response_too_large"),
    ],
)
async def test_controller_does_not_repair_transport_or_oversized_responses(
    tmp_path: Path, failure: Exception
) -> None:
    profile = ollama_profile(max_repairs=2)
    storage = artifact_storage(tmp_path)
    snapshot = storage.snapshot_tickets(RUN_ID, ("a.txt", "b.txt"))
    client = RecordingClient(
        failure, GeneratedReport(report_markdown=f"{GOAL}\na.txt\nb.txt")
    )
    provider = OllamaDecisionProvider(storage, profile, client)  # type: ignore[arg-type]

    with pytest.raises(type(failure)):
        await provider.decide(
            decision_input(profile, ticket_snapshot=snapshot), UnusedUsageReporter()
        )

    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_report_budget_exhaustion_is_classified_after_two_repairs(
    tmp_path: Path,
) -> None:
    profile = ollama_profile(max_repairs=2)
    storage = artifact_storage(tmp_path)
    snapshot = storage.snapshot_tickets(RUN_ID, ("a.txt", "b.txt"))
    client = RecordingClient(
        ModelOutputInvalid("report_too_long"),
        ModelOutputInvalid("report_too_long"),
        ModelOutputInvalid("report_too_long"),
    )
    provider = OllamaDecisionProvider(storage, profile, client)  # type: ignore[arg-type]

    with pytest.raises(ModelBudgetExceeded, match="model report budget exceeded"):
        await provider.decide(
            decision_input(profile, ticket_snapshot=snapshot), UnusedUsageReporter()
        )

    assert len(client.calls) == 3


class RecordingUsageReporter:
    def __init__(self) -> None:
        self.started: list[int] = []
        self.completed: list[dict[str, int | None]] = []

    async def request_started(self, *, estimated_input_tokens: int) -> None:
        self.started.append(estimated_input_tokens)

    async def response_completed(
        self,
        *,
        estimated_input_tokens: int,
        input_tokens: int | None,
        output_tokens: int | None,
        duration_ms: int | None,
    ) -> None:
        self.completed.append(
            {
                "estimated_input_tokens": estimated_input_tokens,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration_ms": duration_ms,
            }
        )


def generation_request(**overrides: object) -> ReportGenerationRequest:
    values: dict[str, object] = {
        "goal": GOAL,
        "tickets": (
            VerifiedTicket(path="a.txt", content="Export is pending."),
            VerifiedTicket(path="b.txt", content="Password reset is confusing."),
        ),
    }
    values.update(overrides)
    return ReportGenerationRequest(**values)


def ollama_envelope(
    content: str, *, include_usage: bool = True, extra: dict[str, object] | None = None
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": "qwen3:4b-instruct-2507-q4_K_M",
        "message": {"role": "assistant", "content": content},
        "done": True,
    }
    if include_usage:
        payload.update(
            {"prompt_eval_count": 111, "eval_count": 42, "total_duration": 5_500_000}
        )
    if extra:
        payload.update(extra)
    return payload


def valid_content() -> str:
    return json.dumps(
        {"report_markdown": f"# Report\n{GOAL}\na.txt\nb.txt"},
        separators=(",", ":"),
    )


def ollama_client(
    transport: httpx.AsyncBaseTransport,
    *,
    auto_identity: bool = True,
    **profile_overrides: object,
) -> OllamaClient:
    if auto_identity:
        transport = VerifiedModelTransport(transport)
    return OllamaClient(
        base_url="http://127.0.0.1:11434",
        profile=ollama_profile(**profile_overrides),
        read_timeout_seconds=150,
        transport=transport,
    )


class VerifiedModelTransport(httpx.AsyncBaseTransport):
    def __init__(self, delegate: httpx.AsyncBaseTransport) -> None:
        self._delegate = delegate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3:4b-instruct-2507-q4_K_M",
                            "digest": "a" * 64,
                        }
                    ]
                },
            )
        return await self._delegate.handle_async_request(request)

    async def aclose(self) -> None:
        await self._delegate.aclose()


@pytest.mark.asyncio
async def test_http_adapter_sends_exact_bounded_structured_request() -> None:
    captured: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        assert request.url == httpx.URL("http://127.0.0.1:11434/api/chat")
        return httpx.Response(200, json=ollama_envelope(valid_content()))

    usage = RecordingUsageReporter()
    result = await ollama_client(httpx.MockTransport(handler)).generate_report(
        generation_request(), usage
    )

    assert result.report_markdown == f"# Report\n{GOAL}\na.txt\nb.txt"
    payload = captured[0]
    assert payload["model"] == "qwen3:4b-instruct-2507-q4_K_M"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["format"] == GeneratedReport.model_json_schema()
    assert payload["options"] == {
        "temperature": 0,
        "num_ctx": 8_192,
        "num_predict": 2_048,
    }
    assert payload["keep_alive"] == "2m"
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    serialized = json.dumps(payload)
    for forbidden in ("OPENAI_API_KEY", "artifact_root", "approval_id", "delivery_id"):
        assert forbidden not in serialized
    assert usage.started and usage.started[0] > 0
    assert usage.completed == [
        {
            "estimated_input_tokens": usage.started[0],
            "input_tokens": 111,
            "output_tokens": 42,
            "duration_ms": 5,
        }
    ]


@pytest.mark.asyncio
async def test_structured_adapter_accepts_json_arrays_for_strict_tuple_fields() -> None:
    content = json.dumps(
        {
            "findings": [
                {
                    "path": "a.txt",
                    "observation": "Export is slow",
                    "evidence_excerpt": "Export remains pending",
                }
            ]
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=ollama_envelope(content))

    result = await ollama_client(httpx.MockTransport(handler)).generate_structured(
        system_message="Return research JSON.",
        user_document={"path": "a.txt"},
        output_type=ResearchNoteBody,
        usage=RecordingUsageReporter(),
    )

    assert tuple(finding.path for finding in result.findings) == ("a.txt",)


@pytest.mark.asyncio
async def test_version_one_generate_report_request_is_byte_equivalent_after_refactor() -> None:
    captured: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=ollama_envelope(valid_content()))

    request = generation_request(repair_category="missing_goal")
    await ollama_client(httpx.MockTransport(handler)).generate_report(
        request, RecordingUsageReporter()
    )

    expected_document = {
        "goal": GOAL,
        "tickets": [ticket.model_dump(mode="json") for ticket in request.tickets],
        "report_requirements": {
            "exact_goal": GOAL,
            "exact_ticket_paths": ["a.txt", "b.txt"],
        },
        "output_schema": GeneratedReport.model_json_schema(),
        "repair_category": "missing_goal",
        "repair_instruction": "Include the exact goal text in report_markdown.",
    }
    assert captured[0]["messages"][1]["content"] == json.dumps(
        expected_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@pytest.mark.asyncio
async def test_http_adapter_revalidates_digest_before_generation() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3:4b-instruct-2507-q4_K_M",
                            "digest": "b" * 64,
                        }
                    ]
                },
            )
        return httpx.Response(200, json=ollama_envelope(valid_content()))

    usage = RecordingUsageReporter()
    with pytest.raises(ModelConfigurationError):
        await ollama_client(
            httpx.MockTransport(handler), auto_identity=False
        ).generate_report(generation_request(), usage)

    assert calls == ["/api/tags"]
    assert usage.started == []
    assert usage.completed == []


@pytest.mark.asyncio
async def test_http_adapter_states_exact_report_contract_and_repair_instruction() -> None:
    captured: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=ollama_envelope(valid_content()))

    await ollama_client(httpx.MockTransport(handler)).generate_report(
        generation_request(repair_category="missing_goal"),
        RecordingUsageReporter(),
    )

    user_document = json.loads(captured[0]["messages"][1]["content"])
    assert user_document["report_requirements"] == {
        "exact_goal": GOAL,
        "exact_ticket_paths": ["a.txt", "b.txt"],
    }
    assert user_document["repair_instruction"] == (
        "Include the exact goal text in report_markdown."
    )


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_http_adapter_accepts_chunked_response_with_missing_usage() -> None:
    body = json.dumps(ollama_envelope(valid_content(), include_usage=False)).encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        midpoint = len(body) // 2
        return httpx.Response(200, stream=ChunkedStream(body[:midpoint], body[midpoint:]))

    usage = RecordingUsageReporter()
    result = await ollama_client(httpx.MockTransport(handler)).generate_report(
        generation_request(), usage
    )

    assert result.report_markdown.startswith("# Report")
    assert usage.completed[0]["input_tokens"] is None
    assert usage.completed[0]["output_tokens"] is None
    assert usage.completed[0]["duration_ms"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "category"),
    [
        (httpx.Response(200, content=b"not-json"), "invalid_envelope"),
        (httpx.Response(200, json={"done": True}), "invalid_envelope"),
        (
            httpx.Response(200, json=ollama_envelope("not-json")),
            "invalid_schema",
        ),
        (
            httpx.Response(
                200,
                json=ollama_envelope(
                    json.dumps({"report_markdown": "", "extra": "forbidden"})
                ),
            ),
            "invalid_schema",
        ),
        (
            httpx.Response(
                200, json=ollama_envelope(json.dumps({"report_markdown": ""}))
            ),
            "invalid_report",
        ),
        (
            httpx.Response(
                200, json=ollama_envelope(json.dumps({"report_markdown": "\x00"}))
            ),
            "invalid_report",
        ),
        (
            httpx.Response(
                200,
                json=ollama_envelope(
                    json.dumps({"report_markdown": "a.txt b.txt but no submitted goal"})
                ),
            ),
            "missing_goal",
        ),
        (
            httpx.Response(
                200,
                json=ollama_envelope(
                    json.dumps({"report_markdown": f"{GOAL}\na.txt"})
                ),
            ),
            "missing_ticket_path",
        ),
    ],
)
async def test_http_adapter_rejects_invalid_output_without_leaking_content(
    response: httpx.Response, category: str, caplog: pytest.LogCaptureFixture
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return response

    with pytest.raises(ModelOutputInvalid) as caught:
        await ollama_client(httpx.MockTransport(handler)).generate_report(
            generation_request(), RecordingUsageReporter()
        )

    assert caught.value.category == category
    assert "submitted goal" not in str(caught.value)
    assert "submitted goal" not in caplog.text


@pytest.mark.asyncio
async def test_http_adapter_rejects_report_and_response_size_limits() -> None:
    report_response = httpx.Response(
        200,
        json=ollama_envelope(
            json.dumps({"report_markdown": f"{GOAL}\na.txt\nb.txt\n" + "x" * 100})
        ),
    )

    async def report_handler(request: httpx.Request) -> httpx.Response:
        return report_response

    with pytest.raises(ModelOutputInvalid) as report_error:
        await ollama_client(
            httpx.MockTransport(report_handler), max_report_chars=64
        ).generate_report(generation_request(), RecordingUsageReporter())
    assert report_error.value.category == "report_too_long"

    async def body_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 131_073)

    with pytest.raises(ModelOutputInvalid) as body_error:
        await ollama_client(httpx.MockTransport(body_handler)).generate_report(
            generation_request(), RecordingUsageReporter()
        )
    assert body_error.value.category == "response_too_large"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "error_type"),
    [
        (httpx.ReadTimeout("secret timeout"), ModelTransportError),
        (httpx.ConnectError("secret disconnect"), ModelTransportError),
        (httpx.Response(302, headers={"location": "http://example.com/secret"}), ModelRequestRejected),
        (httpx.Response(400, content=b"secret client response"), ModelRequestRejected),
        (httpx.Response(503, content=b"secret server response"), ModelTransportError),
    ],
)
async def test_http_adapter_classifies_status_and_transport_without_leaks(
    outcome: httpx.Response | Exception,
    error_type: type[Exception],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    usage = RecordingUsageReporter()
    with pytest.raises(error_type) as caught:
        await ollama_client(httpx.MockTransport(handler)).generate_report(
            generation_request(), usage
        )

    assert "secret" not in str(caught.value)
    assert "secret" not in caplog.text
    assert len(usage.started) == 1
    assert usage.completed == []


@pytest.mark.asyncio
async def test_runtime_identity_verification_checks_version_tag_digest_and_show() -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.11"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3:4b-instruct-2507-q4_K_M",
                            "digest": "a" * 64,
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"details": {}, "model_info": {"x": 1}})

    await ollama_client(
        httpx.MockTransport(handler), auto_identity=False
    ).verify_runtime(
        "0.40.11", "qwen3:4b-instruct-2507-q4_K_M", "a" * 64
    )

    assert calls == [
        ("GET", "/api/version", None),
        ("GET", "/api/tags", None),
        (
            "POST",
            "/api/show",
            {"model": "qwen3:4b-instruct-2507-q4_K_M"},
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_endpoint", ["version", "tags", "show"])
async def test_runtime_identity_mismatch_is_sanitized(bad_endpoint: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            version = "secret-wrong" if bad_endpoint == "version" else "0.40.11"
            return httpx.Response(200, json={"version": version})
        if request.url.path == "/api/tags":
            digest = "b" * 64 if bad_endpoint == "tags" else "a" * 64
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3:4b-instruct-2507-q4_K_M",
                            "digest": digest,
                        }
                    ]
                },
            )
        payload = {"details": {}, "model_info": {}}
        if bad_endpoint == "show":
            payload = {"secret": "wrong"}
        return httpx.Response(200, json=payload)

    with pytest.raises(ModelConfigurationError) as caught:
        await ollama_client(
            httpx.MockTransport(handler), auto_identity=False
        ).verify_runtime(
            "0.40.11", "qwen3:4b-instruct-2507-q4_K_M", "a" * 64
        )

    assert "secret" not in str(caught.value)
