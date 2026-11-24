
"""Q2: Sarah's five most recently viewed products.

Parameters:
    sarah_user_id: Shared MySQL user ID.
    as_of: UTC reference timestamp.

Returns:
    Up to five products with product_id, name,
    and last_viewed_at.

Cache:
    Redis key: recent_views:{user_id}
    TTL: 300 seconds
"""

import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import redis


CACHE_TTL = 300

client = redis.Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    decode_responses=True,
)


def format_utc(value):
    """Convert a datetime or timestamp to UTC milliseconds."""
    if isinstance(value, str):
        value = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

    if not isinstance(value, datetime):
        raise TypeError("Expected a datetime or timestamp")

    if value.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")

    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def fetch_recent_views(user_id, as_of):
    """Call B's MongoDB recent_views interface."""
    path = (
        Path(__file__).resolve().parent.parent
        / "mongo"
        / "interfaces.py"
    )

    if not path.is_file():
        raise RuntimeError(
            "MongoDB interface is not ready: " + str(path)
        )

    spec = importlib.util.spec_from_file_location(
        "shop_mongo_interfaces", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module.recent_views(user_id, as_of)


def q02(sarah_user_id, as_of):
    """Return recent views using Redis cache-aside."""

    user_id = int(sarah_user_id)

    if not 0 < user_id <= 2**53 - 1:
        raise ValueError("Invalid user ID")

    as_of_key = format_utc(as_of)
    key = f"recent_views:{user_id}"

    # 1. Check Redis.
    cached = client.get(key)

    if cached is not None:
        cached_data = json.loads(cached)

        if cached_data.get("as_of") == as_of_key:
            return cached_data["items"]

    # 2. Cache miss: query MongoDB.
    # Pass a UTC datetime to B's interface.
    as_of_datetime = datetime.fromisoformat(
        as_of_key.replace("Z", "+00:00")
    )

    mongo_items = fetch_recent_views(
        user_id, as_of_datetime
    )

    # 3. Convert BSON IDs and dates to JSON-friendly data.
    items = []

    for item in mongo_items:
        items.append({
            "product_id": str(item["product_id"]),
            "name": item["name"],
            "last_viewed_at": format_utc(
                item["last_viewed_at"]
            ),
        })

    # 4. Store results for five minutes.
    payload = {
        "as_of": as_of_key,
        "items": items,
    }

    client.set(
        key,
        json.dumps(payload),
        ex=CACHE_TTL,
    )

    return items
