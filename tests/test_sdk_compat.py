"""Tests for the Apify SDK forward-compat shim (``actor_logger.sdk_compat``).

The Apify platform stamps runs started via its MCP integration with
``meta.origin = "MCP"``, a value absent from the installed SDK's ``MetaOrigin``
enum (true even in the latest apify-shared). The SDK validates every run
response against that strict enum, so an unknown origin crashes
``Actor.set_status_message`` and the terminal status set during ``Actor`` exit.
Importing ``actor_logger`` registers the missing origin(s) so parsing succeeds,
covering every actor in the suite that imports the package.
"""
from __future__ import annotations

import importlib.util

import pytest

HAS_APIFY_SHARED = importlib.util.find_spec("apify_shared") is not None
HAS_APIFY = importlib.util.find_spec("apify") is not None


@pytest.mark.skipif(not HAS_APIFY_SHARED, reason="apify_shared not installed")
def test_importing_package_registers_mcp_origin():
    # Importing the package must apply the shim as a side effect.
    import actor_logger  # noqa: F401

    from apify_shared.consts import MetaOrigin

    assert "MCP" in {m.value for m in MetaOrigin}


@pytest.mark.skipif(not HAS_APIFY, reason="full apify SDK not installed")
def test_actor_run_accepts_mcp_origin():
    import actor_logger  # noqa: F401

    from apify._models import ActorRun
    from pydantic import ValidationError

    try:
        ActorRun.model_validate({"meta": {"origin": "MCP"}})
        origin_errors = []
    except ValidationError as exc:
        origin_errors = [
            ".".join(str(p) for p in e["loc"])
            for e in exc.errors()
            if "origin" in ".".join(str(p) for p in e["loc"])
        ]

    assert origin_errors == []


def test_register_is_idempotent_and_graceful():
    # Must never raise: repeated calls, and a brand-new future value.
    from actor_logger.sdk_compat import register_meta_origins

    register_meta_origins("MCP")
    register_meta_origins("MCP")
    register_meta_origins("ZZ_FUTURE_ORIGIN_TEST")
