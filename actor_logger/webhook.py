"""Shared webhook transport for actor-logger telemetry clients."""

import json
import logging
import os
import threading
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class WebhookLogger:
    """Small stdlib-only JSON webhook transport.

    Reads ACTOR_LOG_WEBHOOK_URL and ACTOR_LOG_API_KEY by default, but accepts
    explicit values so non-Apify services can reuse the same webhook.
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 10,
        join_timeout: float = 5,
    ):
        resolved_url = os.getenv("ACTOR_LOG_WEBHOOK_URL", "") if webhook_url is None else webhook_url
        resolved_key = os.getenv("ACTOR_LOG_API_KEY", "") if api_key is None else api_key
        self.webhook_url = resolved_url.strip()
        self.api_key = resolved_key.strip()
        self.timeout = timeout
        self.join_timeout = join_timeout
        self.enabled = bool(self.webhook_url)

    def post(self, data: dict[str, Any], wait: bool = False) -> bool:
        """POST via background thread. Set wait=True for terminal events."""
        if not self.enabled:
            return False
        try:
            thread = threading.Thread(target=self.post_sync, args=(data,), daemon=True)
            thread.start()
            if wait:
                thread.join(timeout=self.join_timeout)
            return True
        except Exception as e:
            logger.warning("actor-logger: failed to schedule webhook: %s", e)
            return False

    def post_sync(self, data: dict[str, Any]) -> bool:
        """POST synchronously. Returns False for transport failures."""
        if not self.enabled:
            return False
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
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    return True
                logger.warning("actor-logger: webhook returned %d", resp.status)
                return False
        except Exception as e:
            logger.warning("actor-logger: webhook POST failed: [%s] %r", type(e).__name__, e)
            return False
