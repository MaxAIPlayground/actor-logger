"""Tests for helpers module."""

from actor_logger.helpers import sanitize_input, format_duration


class TestSanitizeInput:
    def test_filters_sensitive_keys(self):
        result = sanitize_input({
            "query": "test",
            "password": "secret",
            "api_token": "tok123",
            "proxy": "http://proxy:8080",
        })
        assert "query" in result
        assert "password" not in result
        assert "api_token" not in result
        assert "proxy" not in result

    def test_nested_sensitive_keys(self):
        result = sanitize_input({
            "config": {"apikey": "hidden", "url": "visible"},
        })
        assert "url" in result["config"]
        assert "apikey" not in result["config"]

    def test_truncates_long_lists(self):
        result = sanitize_input({"items": list(range(50))})
        assert len(result["items"]) == 26  # 25 items + "... (50 items)"
        assert "50 items" in result["items"][-1]

    def test_short_lists_untouched(self):
        result = sanitize_input({"items": [1, 2, 3]})
        assert result["items"] == [1, 2, 3]

    def test_empty_input(self):
        assert sanitize_input({}) == {}

    def test_bools_sorted_last(self):
        result = sanitize_input({"enabled": True, "query": "test", "debug": False})
        keys = list(result.keys())
        assert keys.index("query") < keys.index("enabled")
        assert keys.index("query") < keys.index("debug")


class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(5.3) == "5.3s"
        assert format_duration(0.1) == "0.1s"

    def test_minutes(self):
        assert format_duration(125) == "2m 5s"

    def test_hours(self):
        assert format_duration(3725) == "1h 2m"


def test_sanitize_input_strips_second_factor_material():
    """verificationCode is a declared secret input; it must never be transmitted.

    Also guards the over-matching direction: useStoredCredentials is an
    innocuous bool and must survive.
    """
    raw = {
        "password": "hunter2",
        "verificationCode": "123456",
        "verification_code": "123456",
        "passphrase": "correct horse",
        "otp": "999111",
        "username": "user@example.com",
        "useStoredCredentials": True,
        "skipDeduplication": False,
    }
    out = sanitize_input(raw)

    for leaked in ("password", "verificationCode", "verification_code", "passphrase", "otp"):
        assert leaked not in out, f"{leaked} survived sanitize_input"
    assert out["username"] == "user@example.com"
    assert out["useStoredCredentials"] is True
    assert out["skipDeduplication"] is False
