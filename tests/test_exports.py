"""Public package export tests."""

from actor_logger import ActorLogger, RapidApiLogger, WebhookLogger


def test_public_exports_available():
    assert ActorLogger.__name__ == "ActorLogger"
    assert WebhookLogger.__name__ == "WebhookLogger"
    assert RapidApiLogger.__name__ == "RapidApiLogger"
