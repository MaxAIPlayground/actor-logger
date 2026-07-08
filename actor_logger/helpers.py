"""Shared utilities: input sanitization, formatting."""

import json
import os
from pathlib import Path

# Substring-matched against the lowercased key. Keep entries specific enough not
# to swallow innocuous fields (e.g. "credential" would eat useStoredCredentials).
_SENSITIVE_KEYS = {
    "password", "token", "secret", "apikey", "proxy", "api_key", "api_token",
    # One-time / second-factor material. `verificationCode` is a declared secret
    # input on at least one actor and was previously transmitted in the clear.
    "verificationcode", "verification_code", "passphrase", "otp",
}
_LIST_DISPLAY_LIMIT = 25
_actor_json_cache: dict[str, str | None] = {}


def _is_sensitive(key: str) -> bool:
    return any(s in key.lower() for s in _SENSITIVE_KEYS)


def _filter_sensitive(obj):
    if isinstance(obj, dict):
        return {k: _filter_sensitive(v) for k, v in obj.items()
                if not _is_sensitive(k)}
    if isinstance(obj, list):
        return [_filter_sensitive(item) for item in obj]
    return obj


def _truncate_lists(obj):
    if isinstance(obj, dict):
        return {k: _truncate_lists(v) for k, v in obj.items()}
    if isinstance(obj, list) and len(obj) > _LIST_DISPLAY_LIMIT:
        return obj[:_LIST_DISPLAY_LIMIT] + [f"... ({len(obj)} items)"]
    return obj


def _sort_non_bool_first(d: dict) -> dict:
    non_bool = {k: v for k, v in d.items() if not isinstance(v, bool)}
    bools = {k: v for k, v in d.items() if isinstance(v, bool)}
    return {**non_bool, **bools}


def sanitize_input(raw: dict) -> dict:
    """Filter sensitive keys, truncate long lists. Returns cleaned dict."""
    filtered = _filter_sensitive(raw)
    if not filtered:
        return {}
    filtered = _sort_non_bool_first(filtered)
    filtered = _truncate_lists(filtered)
    return filtered


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h {mins}m"


def resolve_actor_id() -> str | None:
    """Resolve actor ID with fallback chain:
    1. ACTOR_FULL_NAME env var (set by Apify in production)
    2. .actor/actor.json 'name' field (always present locally)
    3. Current directory name (last resort)
    """
    name = os.getenv("ACTOR_FULL_NAME")
    if name:
        return name

    cwd = Path.cwd()
    cache_key = str(cwd)
    if cache_key in _actor_json_cache:
        return _actor_json_cache[cache_key]

    for d in [cwd, *cwd.parents]:
        candidate = d / ".actor" / "actor.json"
        if candidate.is_file():
            try:
                actor_name = json.loads(candidate.read_text()).get("name")
                _actor_json_cache[cache_key] = actor_name
                return actor_name
            except (json.JSONDecodeError, OSError):
                break

    fallback = cwd.name
    _actor_json_cache[cache_key] = fallback
    return fallback
