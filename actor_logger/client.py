"""Structured event logging to actor-logging webhook."""

import asyncio
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any

import aiohttp

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
        })

    def log_complete(self, stats: dict[str, Any] | None = None) -> bool:
        """Log run completion with stats (duration_seconds, items, etc.)."""
        if not self.enabled:
            return False
        return self._post({
            "event": "run_complete",
            "stats": stats or {},
            **self._meta(),
        })

    def log_event(self, event_name: str, data: dict[str, Any] | None = None) -> bool:
        """Log a custom event (user_tier_detected, rate_limited, etc.)."""
        if not self.enabled:
            return False
        return self._post({
            "event": event_name,
            **(data or {}),
            **self._meta(),
        })

    def _meta(self) -> dict:
        return {
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

    def _post(self, data: dict) -> bool:
        """Fire-and-forget POST. Uses running event loop if available, otherwise creates one."""
        try:
            try:
                loop = asyncio.get_running_loop()
                asyncio.create_task(self._post_async(data))
                return True
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(self._post_async(data))
                finally:
                    loop.close()
        except Exception as e:
            logger.warning("actor-logger: failed to schedule webhook: %s", e)
            return False

    async def _post_async(self, data: dict) -> bool:
        try:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.webhook_url,
                    data=json.dumps(data, default=str),
                    headers=headers,
                ) as resp:
                    if resp.status == 200:
                        return True
                    logger.warning("actor-logger: webhook returned %d", resp.status)
                    return False
        except Exception as e:
            logger.warning("actor-logger: webhook POST failed: %s", e)
            return False
