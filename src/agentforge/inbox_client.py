"""Verified loopback HTTP adapter for the simulated inbox."""

from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import ValidationError

from agentforge.domain import DeliveryReceipt, DeliveryRequest


class InboxRetryableError(Exception):
    """The receiver was unavailable before a result could be established."""


class InboxUncertainError(Exception):
    """The receiver result cannot be safely interpreted or retried blindly."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class InboxClient:
    MAX_RESPONSE_BYTES = 65_536

    def __init__(self, base_url: str, *, timeout_seconds: float = 3.0) -> None:
        self._url = f"{base_url.rstrip('/')}/api/v1/deliveries"
        self._timeout_seconds = timeout_seconds
        self._opener = build_opener(ProxyHandler({}), _RejectRedirects())

    def deliver(self, request: DeliveryRequest) -> DeliveryReceipt:
        http_request = Request(
            self._url,
            data=request.model_dump_json().encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(
                http_request, timeout=self._timeout_seconds
            ) as response:
                status = response.status
                body = response.read(self.MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            if 500 <= error.code <= 599:
                raise InboxRetryableError("simulated inbox unavailable") from None
            raise InboxUncertainError("simulated inbox rejected delivery") from None
        except (TimeoutError, URLError, OSError):
            raise InboxRetryableError("simulated inbox unavailable") from None

        if status not in {200, 201}:
            raise InboxUncertainError("simulated inbox returned an unexpected status")
        if len(body) > self.MAX_RESPONSE_BYTES:
            raise InboxUncertainError("simulated inbox response exceeded the limit")
        try:
            receipt = DeliveryReceipt.model_validate_json(body)
        except ValidationError:
            raise InboxUncertainError("simulated inbox returned an invalid receipt") from None
        if (
            receipt.operation_id != request.operation_id
            or receipt.artifact_sha256 != request.artifact.sha256
        ):
            raise InboxUncertainError("simulated inbox receipt did not match the request")
        return receipt
