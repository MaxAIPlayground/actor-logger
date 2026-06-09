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
