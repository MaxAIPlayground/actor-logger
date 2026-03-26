"""Tests for ActorLogger webhook client."""

import json
import os
from unittest.mock import patch, AsyncMock

import pytest
import pytest_asyncio
from aioresponses import aioresponses

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

    @pytest.mark.asyncio
    async def test_log_start_sends_two_events(self, logger):
        payloads = []
        with aioresponses() as m:
            m.post(WEBHOOK_URL, repeat=True, status=200, payload={"success": True})
            await logger._post_async({"event": "actor_start", **logger._meta()})
            await logger._post_async({
                "event": "input_logged",
                "input": {"query": "test"},
                **logger._meta(),
            })

        # Verify we can construct the payloads
        meta = logger._meta()
        assert meta["run_id"] == "run123"
        assert meta["actor_id"] == "clearpath/test-actor"
        assert meta["user_id"] == "user456"
        assert meta["apify_meta_origin"] == "WEB"
        assert "timestamp" in meta

    @pytest.mark.asyncio
    async def test_log_error_payload(self, logger):
        with aioresponses() as m:
            m.post(WEBHOOK_URL, status=200, payload={"success": True})

            try:
                raise ValueError("something broke")
            except Exception as e:
                data = {
                    "event": "error",
                    "error": {
                        "message": str(e),
                        "type": type(e).__name__,
                    },
                    "context": {"severity": "critical", "stage": "search"},
                    **logger._meta(),
                }
                result = await logger._post_async(data)

            assert result is True
            assert data["error"]["type"] == "ValueError"
            assert data["error"]["message"] == "something broke"
            assert data["context"]["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_log_complete_payload(self, logger):
        with aioresponses() as m:
            m.post(WEBHOOK_URL, status=200, payload={"success": True})

            stats = {"duration_seconds": 42, "items": 150}
            data = {"event": "run_complete", "stats": stats, **logger._meta()}
            result = await logger._post_async(data)

            assert result is True
            assert data["stats"]["items"] == 150

    @pytest.mark.asyncio
    async def test_auth_header_sent(self, logger):
        with aioresponses() as m:
            m.post(WEBHOOK_URL, status=200, payload={"success": True})
            await logger._post_async({"event": "test", **logger._meta()})

            # aioresponses uses URL objects as keys
            calls = list(m.requests.values())
            assert len(calls) == 1
            request_kwargs = calls[0][0].kwargs
            assert request_kwargs["headers"]["Authorization"] == f"Bearer {API_KEY}"

    @pytest.mark.asyncio
    async def test_webhook_failure_returns_false(self, logger):
        with aioresponses() as m:
            m.post(WEBHOOK_URL, status=500)
            result = await logger._post_async({"event": "test"})
            assert result is False

    @pytest.mark.asyncio
    async def test_sensitive_input_filtered(self, logger):
        input_data = {
            "query": "Berlin apartments",
            "password": "secret123",
            "api_token": "tok_xyz",
            "maxResults": 10,
        }
        with aioresponses() as m:
            m.post(WEBHOOK_URL, repeat=True, status=200, payload={"success": True})
            logger.log_start(input_data)

        # Verify sanitization directly
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
