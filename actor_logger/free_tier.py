"""Free tier usage tracking and enforcement via Apify KV store.

Run counts are stored on the actor OWNER's account so PPE users
cannot delete the KV store to reset their counter.
"""

import logging
import os

try:
    from apify_client import ApifyClientAsync
except ImportError:
    ApifyClientAsync = None

logger = logging.getLogger(__name__)

FREE_TIER_WARNING = (
    "Free plan limited to {limit} {unit}. "
    "Upgrade your plan for unlimited {unit}."
)
FREE_TIER_EXHAUSTED = (
    "Free plan allows {max_runs} runs. "
    "You have used all {max_runs}. Upgrade your plan for unlimited usage."
)


def is_free_tier() -> bool:
    """Check if the current user is on the free tier."""
    return os.getenv("APIFY_USER_IS_PAYING") != "1"


def _get_owner_token() -> str:
    """Get the owner token for KV store operations.

    Reads from OWNER_APIFY_TOKEN env var. This must be set on each actor's
    Apify env vars (the actor owner's API token, not the calling user's).
    """
    token = os.getenv("OWNER_APIFY_TOKEN", "")
    if not token:
        # DEBUG, not WARNING: on the Apify platform the SDK pipes warnings to the
        # user-visible run log, which would leak the env var name + tracking mechanism.
        # Developers can still see this locally via APIFY_LOG_LEVEL=DEBUG.
        logger.debug("OWNER_APIFY_TOKEN not set, free tier tracking disabled")
    return token


class FreeTierGuard:
    """Free tier run limiting + result capping.

    Usage:
        guard = FreeTierGuard(store="my-actor-free-tier", max_runs=5, max_results=50)
        if await guard.is_blocked():
            return  # exits cleanly, no Actor.fail()
        # ... scraping loop ...
        data = await guard.cap_results(data)  # truncate if free tier
        await Actor.push_data(data)
    """

    def __init__(
        self,
        store: str,
        max_runs: int = 5,
        max_results: int = 10,
        unit: str = "items",
    ):
        self.store = store
        self.max_runs = max_runs
        self.max_results = max_results
        self.unit = unit
        self.is_capped = False
        self._items_pushed = 0

    async def is_blocked(self) -> bool:
        """Check if user exceeded lifetime run limit. Returns True if blocked.

        Bypasses for: developer (MY_ACTOR_USER_ID match), TEST/DEVELOPMENT origin,
        paying users. Paying users get cleaned up from the KV store.
        """
        user_id = os.getenv("APIFY_USER_ID")
        if not user_id:
            return False  # local dev

        is_dev = user_id == os.getenv("MY_ACTOR_USER_ID")
        origin = os.getenv("APIFY_META_ORIGIN")
        is_test = origin in ("TEST", "DEVELOPMENT")

        if is_dev or is_test or not is_free_tier():
            # Clean up paying users from KV store
            if not is_free_tier() and self.store:
                await self._cleanup_user(user_id)
            return False

        # Free tier: check and increment run count
        return not await self._check_and_increment(user_id)

    async def cap_results(self, data: list) -> list:
        """Truncate results if free tier user. Call before Actor.push_data()."""
        if not is_free_tier():
            return data

        remaining = max(0, self.max_results - self._items_pushed)
        if len(data) > remaining:
            data = data[:remaining]
            self.is_capped = True

        self._items_pushed += len(data)
        return data

    async def _check_and_increment(self, user_id: str) -> bool:
        """Returns True if run is allowed, False if exhausted."""
        token = _get_owner_token()
        if not token:
            return True  # no token = can't enforce

        try:
            client = ApifyClientAsync(token=token)
            store_info = await client.key_value_stores().get_or_create(name=self.store)
            store = client.key_value_store(store_info["id"])

            record_item = await store.get_record(user_id)
            record = record_item["value"] if record_item else {"run_count": 0}

            if record["run_count"] >= self.max_runs:
                return False

            record["run_count"] = record["run_count"] + 1
            await store.set_record(user_id, record)
            return True
        except Exception:
            return True  # fail open

    async def _cleanup_user(self, user_id: str):
        """Remove upgraded user from KV store."""
        token = _get_owner_token()
        if not token:
            return
        try:
            client = ApifyClientAsync(token=token)
            store_info = await client.key_value_stores().get_or_create(name=self.store)
            store = client.key_value_store(store_info["id"])
            await store.delete_record(user_id)
        except Exception:
            pass
