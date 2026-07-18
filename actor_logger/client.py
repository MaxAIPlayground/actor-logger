"""Structured event logging to actor-logging webhook."""

import os
import traceback
from datetime import datetime, timezone
from typing import Any

from .helpers import resolve_actor_id, sanitize_input
from .webhook import WebhookLogger


def _timeout_secs() -> int | None:
    """The run's CONFIGURED timeout in seconds, or None (local / unlimited).

    Apify gives no duration env var, only ISO instants ACTOR_TIMEOUT_AT and
    ACTOR_STARTED_AT, so it is their delta. Constant across a run's events (uses the
    start, not now), so it is directly comparable to a run's duration. This is the one
    run-option missing from the envelope, and the one that most often explains a
    truncated run (a caller can override the actor default): without it a short timeout
    can only be reverse-engineered from a unit's budget_s. Never raises."""
    try:
        at = os.getenv("ACTOR_TIMEOUT_AT") or os.getenv("APIFY_TIMEOUT_AT")
        st = os.getenv("ACTOR_STARTED_AT") or os.getenv("APIFY_STARTED_AT")
        if not at or not st:
            return None
        end = datetime.fromisoformat(at.replace("Z", "+00:00"))
        start = datetime.fromisoformat(st.replace("Z", "+00:00"))
        secs = round((end - start).total_seconds())
        return secs if secs > 0 else None
    except Exception:
        return None


class ActorLogger:
    """Fire-and-forget structured logging to a centralized webhook.

    Reads configuration from environment variables:
        ACTOR_LOG_WEBHOOK_URL  — webhook endpoint
        ACTOR_LOG_API_KEY      — Bearer token for auth (optional)
    """

    def __init__(self):
        self.webhook = WebhookLogger()
        self.webhook_url = self.webhook.webhook_url
        self.api_key = self.webhook.api_key
        self.enabled = self.webhook.enabled

    def log_start(self, input_data: dict | None = None) -> bool:
        """Log actor start + sanitized input. Call once after Actor.get_input()."""
        if not self.enabled:
            return False
        result = self._post({"event": "actor_start", **self._meta()})
        if input_data is not None:
            result = self._post({
                "event": "input_logged",
                "input": sanitize_input(input_data),
                **self._meta(),
            })
        return result

    def log_error(self, error: Exception | str, context: dict[str, Any] | None = None) -> bool:
        """Log an error with optional context (severity, stage, etc.)."""
        if not self.enabled:
            return False
        if isinstance(error, Exception):
            error_info = {
                "message": str(error),
                "type": type(error).__name__,
                "traceback": traceback.format_exc(),
            }
        else:
            error_info = {
                "message": str(error),
                "type": "Unknown",
                "traceback": None,
            }
        return self._post({
            "event": "error",
            "error": error_info,
            "context": context or {},
            **self._meta(),
        }, wait=True)

    def log_complete(self, stats: dict[str, Any] | None = None) -> bool:
        """Log run completion with stats (duration_seconds, items, etc.).

        Blocks up to 5s to ensure the event is delivered before process exit.
        """
        if not self.enabled:
            return False
        return self._post({
            "event": "run_complete",
            "stats": stats or {},
            **self._meta(),
        }, wait=True)

    def log_event(self, event_name: str, data: dict[str, Any] | None = None, wait: bool = False) -> bool:
        """Log a custom event (user_tier_detected, rate_limited, run_aborted, etc.).

        Set wait=True to block until delivered (useful for terminal events like
        run_aborted / run_failed where the process is about to exit).
        """
        if not self.enabled:
            return False
        return self._post({
            "event": event_name,
            **(data or {}),
            **self._meta(),
        }, wait=wait)

    def _meta(self) -> dict:
        meta = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "run_id": os.getenv("APIFY_ACT_RUN_ID"),
            "actor_id": resolve_actor_id(),
            "user_id": os.getenv("APIFY_USER_ID"),
            "memory_mbytes": os.getenv("APIFY_MEMORY_MBYTES"),
            "build_number": os.getenv("ACTOR_BUILD_NUMBER"),
            "apify_meta_origin": os.getenv("APIFY_META_ORIGIN"),
            "max_total_charge_usd": os.getenv("ACTOR_MAX_TOTAL_CHARGE_USD"),
            "timeout_secs": _timeout_secs(),
            "is_paying": os.getenv("APIFY_USER_IS_PAYING"),
        }
        topic = os.getenv("ACTOR_LOG_TOPIC", "").strip()
        if topic:
            meta["topic"] = topic
        return meta

    def _post(self, data: dict, wait: bool = False) -> bool:
        return self.webhook.post(data, wait=wait)

    def _post_sync(self, data: dict) -> bool:
        return self.webhook.post_sync(data)
