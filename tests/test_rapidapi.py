"""Tests for RapidAPI telemetry wrapper."""

from actor_logger.rapidapi import RapidApiLogger


def fixed_now() -> str:
    return "2026-06-09T12:00:00Z"


class FakeWebhook:
    enabled = True

    def __init__(self):
        self.calls = []

    def post(self, payload, wait=False):
        self.calls.append((payload, wait))
        return True


class DisabledWebhook:
    enabled = False

    def post(self, payload, wait=False):
        raise AssertionError("disabled logger must not post")


def test_request_complete_payload_shape():
    webhook = FakeWebhook()
    logger = RapidApiLogger(service_id="goofish-rapidapi", webhook=webhook, now_fn=fixed_now)

    result = logger.log_request_complete(
        request_id="req-1",
        endpoint="/item/detail",
        method="GET",
        status_code=200,
        latency_ms=123,
        rapidapi_user="user-1",
        subscription="ULTRA",
        input_kind="id",
    )

    assert result is True
    payload, wait = webhook.calls[0]
    assert wait is False
    assert payload["event"] == "request_complete"
    assert payload["timestamp"] == "2026-06-09T12:00:00Z"
    assert payload["run_id"] == "req-1"
    assert payload["actor_id"] == "goofish-rapidapi"
    assert payload["user_id"] == "rapidapi:user-1"
    assert payload["source"] == "rapidapi"
    assert payload["apify_meta_origin"] == "RAPIDAPI"
    assert payload["data"] == {
        "endpoint": "/item/detail",
        "method": "GET",
        "status_code": 200,
        "latency_ms": 123,
        "error_code": None,
        "subscription": "ULTRA",
        "input_kind": "id",
    }


def test_request_complete_accepts_extra_data_without_overwriting_core_fields():
    webhook = FakeWebhook()
    logger = RapidApiLogger(service_id="goofish-rapidapi", webhook=webhook, now_fn=fixed_now)

    logger.log_request_complete(
        request_id="req-1",
        endpoint="/item/detail",
        method="GET",
        status_code=404,
        latency_ms=17,
        error_code="item_unavailable",
        data={"cache": "miss", "status_code": 999},
    )

    payload = webhook.calls[0][0]
    assert payload["data"]["status_code"] == 404
    assert payload["data"]["error_code"] == "item_unavailable"
    assert payload["data"]["cache"] == "miss"


def test_missing_rapidapi_user_leaves_user_id_empty():
    webhook = FakeWebhook()
    logger = RapidApiLogger(service_id="goofish-rapidapi", webhook=webhook, now_fn=fixed_now)

    logger.log_request_complete(
        request_id="req-1",
        endpoint="/health",
        method="GET",
        status_code=200,
        latency_ms=1,
    )

    assert webhook.calls[0][0]["user_id"] is None


def test_string_error_payload_has_no_traceback():
    webhook = FakeWebhook()
    logger = RapidApiLogger(service_id="goofish-rapidapi", webhook=webhook, now_fn=fixed_now)

    result = logger.log_error(
        "signer unavailable",
        request_id="req-2",
        endpoint="/item/detail",
        method="GET",
        status_code=503,
        error_code="service_unavailable",
        rapidapi_user="user-2",
        subscription="PRO",
        context={"retry_after": 30},
    )

    assert result is True
    payload, wait = webhook.calls[0]
    assert wait is True
    assert payload["event"] == "error"
    assert payload["error"] == {
        "message": "signer unavailable",
        "type": "Unknown",
        "traceback": None,
    }
    assert payload["context"]["endpoint"] == "/item/detail"
    assert payload["context"]["status_code"] == 503
    assert payload["context"]["error_code"] == "service_unavailable"
    assert payload["context"]["subscription"] == "PRO"
    assert payload["context"]["retry_after"] == 30


def test_exception_error_payload_omits_traceback_by_default():
    webhook = FakeWebhook()
    logger = RapidApiLogger(service_id="goofish-rapidapi", webhook=webhook, now_fn=fixed_now)

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        logger.log_error(
            exc,
            request_id="req-3",
            endpoint="/item/detail",
            method="GET",
            status_code=502,
            error_code="upstream_error",
        )

    payload = webhook.calls[0][0]
    assert payload["error"]["message"] == "boom"
    assert payload["error"]["type"] == "RuntimeError"
    assert payload["error"]["traceback"] is None


def test_exception_error_payload_can_include_traceback_for_internal_errors():
    webhook = FakeWebhook()
    logger = RapidApiLogger(service_id="goofish-rapidapi", webhook=webhook, now_fn=fixed_now)

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        logger.log_error(
            exc,
            request_id="req-4",
            endpoint="/item/detail",
            method="GET",
            status_code=502,
            error_code="upstream_error",
            include_traceback=True,
        )

    traceback_text = webhook.calls[0][0]["error"]["traceback"]
    assert "RuntimeError: boom" in traceback_text


def test_disabled_logger_returns_false_without_posting():
    logger = RapidApiLogger(service_id="goofish-rapidapi", webhook=DisabledWebhook(), now_fn=fixed_now)

    result = logger.log_request_complete(
        request_id="req-5",
        endpoint="/health",
        method="GET",
        status_code=200,
        latency_ms=1,
    )

    assert result is False
