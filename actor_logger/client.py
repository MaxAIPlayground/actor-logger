"""Structured event logging to actor-logging webhook."""

import json
import logging
import os
import threading
import traceback
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any

from .helpers import resolve_actor_id, sanitize_input

logger = logging.getLogger(__name__)


class ActorLogger:
    """Fire-and-forget structured logging to a centralized webhook.

    Reads configuration from environment variables:
        ACTOR_LOG_WEBHOOK_URL  — webhook endpoint
        ACTOR_LOG_API_KEY      — Bearer token for auth (optional)
    """

    def __init__(self):
        self.webhook_url = os.getenv("ACTOR_LOG_WEBHOOK_URL", "").strip()
        self.api_key = os.getenv("ACTOR_LOG_API_KEY", "").strip()
        self.enabled = bool(self.webhook_url)

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
            "is_paying": os.getenv("APIFY_USER_IS_PAYING"),
        }
        topic = os.getenv("ACTOR_LOG_TOPIC", "").strip()
        if topic:
            meta["topic"] = topic
        return meta

    def _post(self, data: dict, wait: bool = False) -> bool:
        """POST via background thread. Set wait=True to block until delivered."""
        try:
            thread = threading.Thread(target=self._post_sync, args=(data,), daemon=True)
            thread.start()
            if wait:
                thread.join(timeout=5)
            return True
        except Exception as e:
            logger.warning("actor-logger: failed to schedule webhook: %s", e)
            return False

    def _post_sync(self, data: dict) -> bool:
        try:
            body = json.dumps(data, default=str).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return True
                logger.warning("actor-logger: webhook returned %d", resp.status)
                return False
        except Exception as e:
            logger.warning("actor-logger: webhook POST failed: [%s] %r", type(e).__name__, e)
            return False
