from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

from agentforge.domain import ArtifactRef, DeliveryRequest


def _client_module():
    try:
        import agentforge.inbox_client as inbox_client
    except ModuleNotFoundError:
        pytest.fail("agentforge.inbox_client does not exist")
    return inbox_client


def delivery_request() -> DeliveryRequest:
    return DeliveryRequest(
        operation_id=uuid4(),
        action_hash="b" * 64,
        artifact=ArtifactRef(
            path="reports/run/support-report.md",
            sha256="86f80670834fa52bc574e02c7deea10c6243b3058d44ff9f8dbfebe7fa18a5f5",
            size_bytes=17,
            media_type="text/markdown",
        ),
        content="# Support Report\n",
    )


@contextmanager
def response_server(status: int, body: bytes, *, location: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if location is not None:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.parametrize("status", [200, 201])
def test_client_accepts_only_matching_receipt(status: int) -> None:
    module = _client_module()
    request = delivery_request()
    body = json.dumps(
        {
            "delivery_id": str(uuid4()),
            "operation_id": str(request.operation_id),
            "artifact_sha256": request.artifact.sha256,
            "committed_at": datetime.now(UTC).isoformat(),
        }
    ).encode()
    with response_server(status, body) as base_url:
        receipt = module.InboxClient(base_url).deliver(request)

    assert receipt.operation_id == request.operation_id
    assert receipt.artifact_sha256 == request.artifact.sha256


def test_client_never_routes_loopback_delivery_through_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _client_module()
    request = delivery_request()
    body = json.dumps(
        {
            "delivery_id": str(uuid4()),
            "operation_id": str(request.operation_id),
            "artifact_sha256": request.artifact.sha256,
            "committed_at": datetime.now(UTC).isoformat(),
        }
    ).encode()
    with response_server(502, b"proxy must not receive loopback traffic") as proxy_url:
        monkeypatch.setenv("HTTP_PROXY", proxy_url)
        monkeypatch.setenv("HTTPS_PROXY", proxy_url)
        monkeypatch.setenv("NO_PROXY", "")
        with response_server(201, body) as base_url:
            receipt = module.InboxClient(base_url).deliver(request)

    assert receipt.operation_id == request.operation_id


@pytest.mark.parametrize("status", [500, 503])
def test_client_classifies_server_failure_as_retryable(status: int) -> None:
    module = _client_module()
    with response_server(status, b"{}") as base_url:
        with pytest.raises(module.InboxRetryableError):
            module.InboxClient(base_url).deliver(delivery_request())


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (409, b"{}"),
        (200, b"not-json"),
        (200, b"x" * 70_000),
    ],
    ids=["conflict", "malformed-json", "oversized-response"],
)
def test_client_classifies_conflict_or_unverifiable_response_as_uncertain(
    status: int, body: bytes
) -> None:
    module = _client_module()
    with response_server(status, body) as base_url:
        with pytest.raises(module.InboxUncertainError):
            module.InboxClient(base_url).deliver(delivery_request())


def test_client_does_not_follow_redirects() -> None:
    module = _client_module()
    with response_server(302, b"{}", location="http://example.com/") as base_url:
        with pytest.raises(module.InboxUncertainError):
            module.InboxClient(base_url).deliver(delivery_request())
