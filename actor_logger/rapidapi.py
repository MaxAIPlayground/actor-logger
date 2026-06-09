"""Reusable telemetry wrapper for RapidAPI provider backends."""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any, Callable

from .webhook import WebhookLogger


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RapidApiLogger:
    """Structured request/error logging for RapidAPI provider services."""

    def __init__(
        self,
        *,
        service_id: str,
        webhook: WebhookLogger | None = None,
        source: str = "rapidapi",
        origin: str = "RAPIDAPI",
        topic: str | None = None,
        now_fn: Callable[[], str] = _utc_now,
    ):
        self.service_id = service_id
        self.webhook = webhook or WebhookLogger()
        self.enabled = self.webhook.enabled
        self.source = source
        self.origin = origin
        self.topic = topic
        self.now_fn = now_fn

    def log_request_complete(
        self,
        *,
        request_id: str,
        endpoint: str,
        method: str,
        status_code: int,
        latency_ms: int,
        error_code: str | None = None,
        rapidapi_user: str | None = None,
        subscription: str | None = None,
        input_kind: str | None = None,
        data: dict[str, Any] | None = None,
        wait: bool = False,
    ) -> bool:
        """Log one completed HTTP request."""
        if not self.enabled:
            return False
        request_data = dict(data or {})
        request_data.update({
            "endpoint": endpoint,
            "method": method,
            "status_code": int(status_code),
            "latency_ms": int(latency_ms),
            "error_code": error_code,
            "subscription": subscription,
            "input_kind": input_kind,
        })
        return self.webhook.post({
            "event": "request_complete",
            "data": request_data,
            **self._meta(request_id=request_id, rapidapi_user=rapidapi_user),
        }, wait=wait)

    def log_error(
        self,
        error: Exception | str,
        *,
        request_id: str,
        endpoint: str,
        method: str,
        status_code: int,
        error_code: str,
        rapidapi_user: str | None = None,
        subscription: str | None = None,
        context: dict[str, Any] | None = None,
        include_traceback: bool = False,
        wait: bool = True,
    ) -> bool:
        """Log an actionable error event.

        Expected provider errors should pass strings or exceptions with
        include_traceback=False. Only unexpected internal exceptions should
        set include_traceback=True.
        """
        if not self.enabled:
            return False
        error_context = dict(context or {})
        error_context.update({
            "endpoint": endpoint,
            "method": method,
            "status_code": int(status_code),
            "error_code": error_code,
            "subscription": subscription,
        })
        return self.webhook.post({
            "event": "error",
            "error": self._error_info(error, include_traceback=include_traceback),
            "context": error_context,
            **self._meta(request_id=request_id, rapidapi_user=rapidapi_user),
        }, wait=wait)

    def _meta(self, *, request_id: str, rapidapi_user: str | None = None) -> dict[str, Any]:
        user = (rapidapi_user or "").strip()
        meta = {
            "timestamp": self.now_fn(),
            "run_id": request_id,
            "actor_id": self.service_id,
            "user_id": f"rapidapi:{user}" if user else None,
            "source": self.source,
            "apify_meta_origin": self.origin,
        }
        if self.topic:
            meta["topic"] = self.topic
        return meta

    @staticmethod
    def _error_info(error: Exception | str, *, include_traceback: bool) -> dict[str, Any]:
        if isinstance(error, Exception):
            formatted_traceback = None
            if include_traceback:
                formatted_traceback = "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                )
                if not formatted_traceback.strip():
                    formatted_traceback = None
            return {
                "message": str(error),
                "type": type(error).__name__,
                "traceback": formatted_traceback,
            }
        return {
            "message": str(error),
            "type": "Unknown",
            "traceback": None,
        }
