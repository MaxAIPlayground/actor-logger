"""Shared webhook transport for actor-logger telemetry clients."""

import atexit
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class _PermanentPostError(Exception):
    """A 4xx: the event will be rejected identically on every retry."""

# Delivery was best-effort to a fault: ONE attempt, and fire-and-forget POSTs ran on daemon
# threads that the interpreter kills at process exit. A failing actor could therefore leave NO
# telemetry at all (observed 2026-07-15: a resale run failed on a logged path and produced not
# even an actor_start, while a sibling actor logged fine the same day). Silent loss makes every
# error count a lower bound, so a real bug can read as "no errors".
#
# Two bounded fixes: retry transient failures, and give in-flight POSTs a chance to land at exit.
# Failures deliberately stay on logger.debug — these run inside actors whose user-visible run log
# must never expose internals, so telemetry problems must not surface there.
#
# TELEMETRY MUST NOT TAX THE PRODUCT PATH. Terminal events (log_error / log_complete) fire at the
# last instant before the process exits, so they cannot be pure fire-and-forget — there is no
# "later" to flush into, and not waiting at all is how they were lost. But the wait must stay
# SMALL: an actor's exit is billed compute, so a slow/hung webhook would otherwise charge the whole
# fleet for zero user value. Budget: POST_TRIES x POST_TIMEOUT_S + backoff <= JOIN_TIMEOUT_S,
# ~10.5s worst case. Measured real round-trip (DNS+TCP+TLS+dispatch) is 0.11-0.19s, so a 3s
# per-attempt timeout is >10x headroom even allowing for cross-Atlantic RTT and the DB insert.
#
# Losing an event is ACCEPTABLE here, because loss is already visible: `clearpath db health`
# reconciles `N start · M done` and classifies the delta as `no-terminal`. Detecting loss after
# the fact is strictly cheaper than every run paying to prevent it.
#
# All four are env-tunable so ONE actor can be adjusted without touching this library, which the
# whole fleet pins at @master.
POST_TRIES = int(os.getenv("ACTOR_LOG_POST_TRIES", "3"))
POST_TIMEOUT_S = float(os.getenv("ACTOR_LOG_POST_TIMEOUT", "3"))
JOIN_TIMEOUT_S = float(os.getenv("ACTOR_LOG_JOIN_TIMEOUT", "12"))
FLUSH_TIMEOUT_S = float(os.getenv("ACTOR_LOG_FLUSH_TIMEOUT", "5"))

_inflight: set[threading.Thread] = set()
_inflight_lock = threading.Lock()


@atexit.register
def _flush_inflight() -> None:
    """Give fire-and-forget POSTs a bounded chance to land before the process dies.

    atexit runs while daemon threads are still alive; without this they are killed mid-POST and
    the event is lost. Costs nothing when the POSTs already finished (the common case).
    """
    with _inflight_lock:
        threads = list(_inflight)
    if not threads:
        return
    deadline = time.monotonic() + FLUSH_TIMEOUT_S
    for t in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.debug("actor-logger: flush budget exhausted, %d POST(s) may be lost", len(threads))
            return
        t.join(timeout=remaining)


def _unquote(value: str) -> str:
    """Strip whitespace and any surrounding quote characters.

    A URL or bearer token never legitimately starts or ends with a quote, so
    stripping unconditionally also repairs the unbalanced `"https://…` case.
    """
    return value.strip().strip("\"'").strip()


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
        # None = take the env-tunable default. Explicit values still win, so a long-lived
        # non-actor service (which can afford to wait) can opt out of the tight actor budget.
        timeout: float | None = None,
        # Must cover POST_TRIES attempts, else a waited terminal event is abandoned mid-retry
        # (it was 5 while `timeout` alone was 10). Must ALSO stay small: this is billed exit time.
        join_timeout: float | None = None,
    ):
        resolved_url = os.getenv("ACTOR_LOG_WEBHOOK_URL", "") if webhook_url is None else webhook_url
        resolved_key = os.getenv("ACTOR_LOG_API_KEY", "") if api_key is None else api_key
        # Secrets pasted with their .env quotes still attached ('"https://…"')
        # otherwise reach urllib as scheme `"https` and every POST dies.
        self.webhook_url = _unquote(resolved_url)
        self.api_key = _unquote(resolved_key)
        self.timeout = POST_TIMEOUT_S if timeout is None else timeout
        self.join_timeout = JOIN_TIMEOUT_S if join_timeout is None else join_timeout
        self.enabled = bool(self.webhook_url)

    def post(self, data: dict[str, Any], wait: bool = False) -> bool:
        """POST via background thread. Set wait=True for terminal events.

        When wait=True, returns the actual POST result (True on HTTP 200). When
        wait=False, returns True if the thread was scheduled. Delivery is then
        best-effort because the daemon thread may be killed on process exit.
        """
        if not self.enabled:
            return False
        result: dict[str, bool] = {"ok": False}

        thread_box: dict[str, threading.Thread] = {}

        def target() -> None:
            try:
                result["ok"] = self.post_sync(data)
            finally:
                t = thread_box.get("t")
                if t is not None:
                    with _inflight_lock:
                        _inflight.discard(t)

        try:
            thread = threading.Thread(target=target, daemon=True)
            thread_box["t"] = thread
            # Tracked BEFORE start so the atexit flush can never miss a live POST.
            with _inflight_lock:
                _inflight.add(thread)
            thread.start()
            if wait:
                thread.join(timeout=self.join_timeout)
                if thread.is_alive():
                    logger.debug("actor-logger: webhook POST exceeded %ss timeout", self.join_timeout)
                    return False
                return result["ok"]
            return True
        except Exception as e:
            t = thread_box.get("t")
            if t is not None:
                with _inflight_lock:
                    _inflight.discard(t)
            logger.debug("actor-logger: failed to schedule webhook: %s", e)
            return False

    def post_sync(self, data: dict[str, Any], *, tries: int | None = None) -> bool:
        """POST synchronously, retrying transient failures. Returns False once exhausted.

        A single attempt meant one blip lost the event forever, with no way to notice. Retries
        are cheap here: this is one small POST per lifecycle event, not a hot path. 4xx is NOT
        retried — a rejected payload or a bad key fails the same way every time.
        """
        if not self.enabled:
            return False
        attempts = POST_TRIES if tries is None else tries
        for i in range(max(1, attempts)):
            last = i + 1 >= max(1, attempts)
            try:
                if self._attempt(data):
                    return True
            except _PermanentPostError as e:
                logger.debug("actor-logger: webhook rejected the event, not retrying: %s", e)
                return False
            except Exception as e:
                logger.debug("actor-logger: webhook POST failed: [%s] %r", type(e).__name__, e)
            if not last:
                time.sleep(0.5 * (2 ** i))          # 0.5s, 1s — bounded, never on the last try
        return False

    def _attempt(self, data: dict[str, Any]) -> bool:
        """One POST. True on 200. Raises _PermanentPostError on 4xx (a rejected payload or a bad
        key fails identically every time, so retrying only burns the flush budget). Anything else
        returns False or raises, and post_sync retries it.

        NOTE urlopen RAISES HTTPError for >=400 rather than returning a response, so the status
        classification has to live in the except branch, not after the `with`.
        """
        body = json.dumps(data, default=str).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    return True
                logger.debug("actor-logger: webhook returned %d", resp.status)
                return False
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise _PermanentPostError(f"HTTP {e.code}") from e
            logger.debug("actor-logger: webhook returned %d", e.code)
            return False                        # 5xx: server-side, worth a retry
