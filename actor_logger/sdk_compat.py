"""Forward-compat shims for the Apify Python SDK.

The Apify platform can stamp a run with a ``meta.origin`` value newer than the
installed SDK's ``MetaOrigin`` enum. A run started through Apify's MCP
integration carries ``origin = "MCP"``, which is absent from the enum in
current ``apify`` / ``apify-shared`` releases (and on their ``master`` branch).

The SDK validates *every* run response against that strict enum
(``ActorRun.model_validate``). An unknown origin therefore raises a pydantic
``ValidationError`` inside any call that parses a run -- notably
``Actor.set_status_message`` and the terminal status message the SDK sets while
the ``Actor`` context exits. The result is that an otherwise-fine run fails.

A version bump does not help: the value is missing from the latest published
SDK and from upstream ``master`` too. Instead we register the known-missing
origin value(s) into the enum at import time so parsing succeeds. Registration
is idempotent (a value already present -- added here or shipped by a future SDK
-- is skipped, so this becomes a harmless no-op once Apify ships the fix) and
degrades to a no-op if the enum internals ever change or ``apify_shared`` is not
installed (e.g. when this package is used outside an actor).

Importing ``actor_logger`` applies the shim as a side effect, so every actor in
the suite that imports the package is covered with no per-actor change.
"""
from __future__ import annotations

# Origin values the platform emits that current SDK enums do not yet include.
# Extend this tuple if the platform introduces further origins.
_MISSING_META_ORIGINS = ("MCP",)


def register_meta_origins(*values: str) -> None:
    """Register ``values`` as members of the SDK's ``MetaOrigin`` enum.

    Idempotent and failure-swallowing: a value already known is skipped, and any
    inability to mutate the enum (different internals, missing dependency) leaves
    the enum untouched so import can never break an actor.
    """
    try:
        from apify_shared.consts import MetaOrigin
    except Exception:
        return

    for value in values:
        try:
            if value in MetaOrigin._value2member_map_:  # already known
                continue
            member = str.__new__(MetaOrigin, value)
            member._name_ = value
            member._value_ = value
            # Keep the enum fully consistent: lookup, reverse-lookup, and
            # iteration each read a different internal structure.
            MetaOrigin._member_map_[value] = member
            MetaOrigin._value2member_map_[value] = member
            if value not in MetaOrigin._member_names_:
                MetaOrigin._member_names_.append(value)
        except Exception:
            # Enum internals differ on this build; leave the enum untouched.
            continue


# Apply on import.
register_meta_origins(*_MISSING_META_ORIGINS)
