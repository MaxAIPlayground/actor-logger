"""Tests for ActorLogger webhook client."""

import json
import os
from unittest.mock import patch, MagicMock
from urllib.error import URLError

import pytest

from actor_logger import ActorLogger


WEBHOOK_URL = "https://test.example.com/api/actor-logs"
API_KEY = "test-key-123"


@pytest.fixture(autouse=True)
def env_vars():
    with patch.dict(os.environ, {
        "ACTOR_LOG_WEBHOOK_URL": WEBHOOK_URL,
        "ACTOR_LOG_API_KEY": API_KEY,
        "APIFY_ACT_RUN_ID": "run123",
        "ACTOR_FULL_NAME": "clearpath/test-actor",
        "APIFY_USER_ID": "user456",
        "ACTOR_BUILD_NUMBER": "0.0.1",
        "APIFY_META_ORIGIN": "WEB",
    }):
        yield


@pytest.fixture
def logger():
    return ActorLogger()


class TestActorLogger:
    def test_disabled_without_url(self):
        with patch.dict(os.environ, {"ACTOR_LOG_WEBHOOK_URL": ""}):
            lg = ActorLogger()
            assert not lg.enabled
            assert lg.log_start() is False

    def test_enabled_with_url(self, logger):
        assert logger.enabled

    def test_log_start_fires_post(self, logger):
        with patch.object(logger, "_post", return_value=True) as mock_post:
            logger.log_start({"query": "test"})
            assert mock_post.call_count == 2
            events = [call.args[0]["event"] for call in mock_post.call_args_list]
            assert events == ["actor_start", "input_logged"]

    def test_log_error_payload(self, logger):
        with patch.object(logger, "_post", return_value=True) as mock_post:
            try:
                raise ValueError("something broke")
            except Exception as e:
                logger.log_error(e, {"severity": "critical", "stage": "search"})

            payload = mock_post.call_args[0][0]
            assert payload["event"] == "error"
            assert payload["error"]["type"] == "ValueError"
            assert payload["error"]["message"] == "something broke"
            assert payload["context"]["severity"] == "critical"

    def test_log_complete_payload(self, logger):
        with patch.object(logger, "_post", return_value=True) as mock_post:
            logger.log_complete({"duration_seconds": 42, "items": 150})

            payload = mock_post.call_args[0][0]
            assert payload["event"] == "run_complete"
            assert payload["stats"]["items"] == 150

    def test_meta_fields(self, logger):
        meta = logger._meta()
        assert meta["run_id"] == "run123"
        assert meta["actor_id"] == "clearpath/test-actor"
        assert meta["user_id"] == "user456"
        assert meta["apify_meta_origin"] == "WEB"
        assert "timestamp" in meta
        assert "timeout_secs" in meta          # always present, even when None

    def test_timeout_secs_computed_from_instants(self, logger):
        with patch.dict(os.environ, {
            "ACTOR_STARTED_AT": "2026-07-17T06:22:45.000Z",
            "ACTOR_TIMEOUT_AT": "2026-07-17T06:37:45.000Z",   # +900s
        }):
            assert logger._meta()["timeout_secs"] == 900

    def test_timeout_secs_none_without_env(self, logger):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ACTOR_TIMEOUT_AT", None)
            os.environ.pop("APIFY_TIMEOUT_AT", None)
            assert logger._meta()["timeout_secs"] is None

    def test_timeout_secs_none_on_garbage(self, logger):
        with patch.dict(os.environ, {
            "ACTOR_STARTED_AT": "not-a-date",
            "ACTOR_TIMEOUT_AT": "also-bad",
        }):
            assert logger._meta()["timeout_secs"] is None   # never raises

    def test_post_sync_sends_request(self, logger):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            result = logger._post_sync({"event": "test", **logger._meta()})

        assert result is True
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert req.full_url == WEBHOOK_URL
        assert req.get_header("Authorization") == f"Bearer {API_KEY}"
        assert req.get_header("Content-type") == "application/json"

    def test_post_sync_failure_returns_false(self, logger):
        with patch("urllib.request.urlopen", side_effect=URLError("connection refused")):
            result = logger._post_sync({"event": "test"})
        assert result is False

    def test_post_delegates_to_webhook_transport(self, logger):
        with patch.object(logger.webhook, "post", return_value=True) as mock_post:
            result = logger._post({"event": "test"}, wait=True)

        assert result is True
        mock_post.assert_called_once_with({"event": "test"}, wait=True)

    def test_sensitive_input_filtered(self, logger):
        input_data = {
            "query": "Berlin apartments",
            "password": "secret123",
            "api_token": "tok_xyz",
            "maxResults": 10,
        }
        from actor_logger.helpers import sanitize_input
        sanitized = sanitize_input(input_data)
        assert "query" in sanitized
        assert "maxResults" in sanitized
        assert "password" not in sanitized
        assert "api_token" not in sanitized


class TestActorIdResolution:
    def test_uses_env_var_first(self):
        with patch.dict(os.environ, {"ACTOR_FULL_NAME": "zen-studio/haraj-scraper"}):
            from actor_logger.helpers import resolve_actor_id
            assert resolve_actor_id() == "zen-studio/haraj-scraper"

    def test_falls_back_to_dir_name(self):
        with patch.dict(os.environ, {}, clear=True):
            from actor_logger.helpers import resolve_actor_id, _actor_json_cache
            _actor_json_cache.clear()
            name = resolve_actor_id()
            assert isinstance(name, str)
            assert len(name) > 0
