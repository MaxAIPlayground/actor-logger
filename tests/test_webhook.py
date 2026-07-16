"""Tests for shared webhook transport."""

import os
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from actor_logger.webhook import WebhookLogger


WEBHOOK_URL = "https://test.example.com/api/actor-logs"
API_KEY = "test-key-123"


def _response(status: int):
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_disabled_without_url():
    with patch.dict(os.environ, {"ACTOR_LOG_WEBHOOK_URL": ""}):
        logger = WebhookLogger()

    assert logger.enabled is False
    assert logger.post({"event": "test"}) is False
    assert logger.post_sync({"event": "test"}) is False


def test_explicit_config_overrides_environment():
    with patch.dict(os.environ, {"ACTOR_LOG_WEBHOOK_URL": "https://wrong.example.com", "ACTOR_LOG_API_KEY": "wrong"}):
        logger = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)

    assert logger.enabled is True
    assert logger.webhook_url == WEBHOOK_URL
    assert logger.api_key == API_KEY


def test_quoted_env_values_are_unwrapped():
    """A secret stored with its .env quotes attached must still be usable.

    Unbalanced (`"https://…`) is the observed real-world case: urllib parses
    the scheme as `"https` and every POST dies with URLError.
    """
    for raw in (f'"{WEBHOOK_URL}"', f"'{WEBHOOK_URL}'", f'"{WEBHOOK_URL}', f' {WEBHOOK_URL}" '):
        with patch.dict(os.environ, {"ACTOR_LOG_WEBHOOK_URL": raw, "ACTOR_LOG_API_KEY": f'"{API_KEY}"'}):
            logger = WebhookLogger()

        assert logger.webhook_url == WEBHOOK_URL, raw
        assert logger.api_key == API_KEY
        assert logger.enabled is True


def test_post_sync_sends_authorized_json_request():
    logger = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)

    with patch("urllib.request.urlopen", return_value=_response(200)) as mock_urlopen:
        result = logger.post_sync({"event": "test", "source": "rapidapi"})

    assert result is True
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == WEBHOOK_URL
    assert req.get_header("Authorization") == f"Bearer {API_KEY}"
    assert req.get_header("Content-type") == "application/json"
    assert mock_urlopen.call_args.kwargs["timeout"] == 10


def test_post_sync_returns_false_for_non_200():
    logger = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)

    with patch("urllib.request.urlopen", return_value=_response(503)):
        result = logger.post_sync({"event": "test"})

    assert result is False


def test_post_sync_failure_returns_false():
    logger = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)

    with patch("urllib.request.urlopen", side_effect=URLError("connection refused")):
        result = logger.post_sync({"event": "test"})

    assert result is False


def test_post_wait_true_returns_actual_delivery_result():
    logger = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)

    with patch.object(logger, "post_sync", return_value=False):
        result = logger.post({"event": "terminal"}, wait=True)

    assert result is False


def test_post_wait_false_returns_scheduled_result():
    logger = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)

    with patch.object(logger, "post_sync", return_value=False):
        result = logger.post({"event": "best_effort"}, wait=False)

    assert result is True


# --- delivery guarantee -------------------------------------------------
# Silent loss made every error count a lower bound: on 2026-07-15 a failing actor run
# produced NO telemetry at all (not even actor_start) on a path that explicitly calls
# log_error, while a sibling actor logged fine the same day.


def test_post_sync_retries_transient_failures():
    """One blip used to lose the event forever."""
    logger_ = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)
    with patch("actor_logger.webhook.urllib.request.urlopen") as urlopen, \
            patch("actor_logger.webhook.time.sleep"):
        urlopen.side_effect = [URLError("boom"), URLError("boom"), _response(200)]
        assert logger_.post_sync({"event": "error"}) is True
        assert urlopen.call_count == 3


def test_post_sync_gives_up_after_tries_and_reports_false():
    logger_ = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)
    with patch("actor_logger.webhook.urllib.request.urlopen") as urlopen, \
            patch("actor_logger.webhook.time.sleep"):
        urlopen.side_effect = URLError("down")
        assert logger_.post_sync({"event": "error"}, tries=3) is False
        assert urlopen.call_count == 3


def test_post_sync_does_not_retry_4xx():
    """A rejected payload or a bad key fails the same way every time — retrying only
    burns the exit flush budget."""
    from urllib.error import HTTPError
    logger_ = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)
    with patch("actor_logger.webhook.urllib.request.urlopen") as urlopen, \
            patch("actor_logger.webhook.time.sleep"):
        urlopen.side_effect = HTTPError(WEBHOOK_URL, 401, "unauthorized", {}, None)
        assert logger_.post_sync({"event": "error"}) is False
        assert urlopen.call_count == 1, "4xx must not be retried"


def test_post_sync_retries_5xx():
    from urllib.error import HTTPError
    logger_ = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)
    with patch("actor_logger.webhook.urllib.request.urlopen") as urlopen, \
            patch("actor_logger.webhook.time.sleep"):
        urlopen.side_effect = [HTTPError(WEBHOOK_URL, 502, "bad gateway", {}, None), _response(200)]
        assert logger_.post_sync({"event": "error"}) is True
        assert urlopen.call_count == 2


def test_join_timeout_covers_the_retry_budget():
    """join_timeout was 5 while a single attempt could take `timeout`=10, so a waited
    terminal event was abandoned mid-flight and then killed at exit."""
    logger_ = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)
    assert logger_.join_timeout >= logger_.timeout, "a waited POST is abandoned before it can finish"


def test_inflight_posts_are_tracked_then_released_for_the_exit_flush():
    """log_start posts fire-and-forget; without the atexit flush its daemon thread is
    killed at process exit and the event vanishes."""
    from actor_logger import webhook as wh
    logger_ = WebhookLogger(webhook_url=WEBHOOK_URL, api_key=API_KEY)
    with patch("actor_logger.webhook.urllib.request.urlopen", return_value=_response(200)):
        assert logger_.post({"event": "actor_start"}, wait=True) is True
    assert wh._inflight == set(), "finished POSTs must be untracked, else flush waits on corpses"
    wh._flush_inflight()          # no-op when nothing is in flight
