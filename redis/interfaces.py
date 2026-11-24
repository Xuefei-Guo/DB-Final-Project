
import json
import secrets
from datetime import datetime, timezone

import redis


SESSION_TTL = 1800
RECENT_VIEWS_TTL = 300

VALID_DEVICES = {"phone", "tablet", "laptop", "desktop"}

client = redis.Redis(
    host="localhost",
    port=6379,
    decode_responses=True,
)


def utc_now():
    """Return the current UTC time in RFC 3339 format."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def create_session(user_id: int, device: str) -> str:
    """Create a session for an authenticated user."""

    if not 0 < user_id <= 2**53 - 1:
        raise ValueError("Invalid user ID")

    if device not in VALID_DEVICES:
        raise ValueError("Invalid device")

    session_id = secrets.token_urlsafe(32)
    now = utc_now()

    session_data = {
        "user_id": str(user_id),
        "device": device,
        "issued_at": now,
        "last_seen_at": now,
    }

    client.set(
        f"session:{session_id}",
        json.dumps(session_data),
        ex=SESSION_TTL,
        nx=True,
    )

    return session_id


READ_SESSION_SCRIPT = """
local value = redis.call('GET', KEYS[1])

if not value then
    return nil
end

local session = cjson.decode(value)

session['last_seen_at'] = ARGV[1]

redis.call(
    'SET',
    KEYS[1],
    cjson.encode(session),
    'EX',
    tonumber(ARGV[2])
)

return cjson.encode(session)
"""


def read_session(session_id: str):
    """Read a session and atomically refresh its inactivity TTL."""

    key = f"session:{session_id}"

    result = client.eval(
        READ_SESSION_SCRIPT,
        1,
        key,
        utc_now(),
        SESSION_TTL,
    )

    if result is None:
        return None

    return json.loads(result)


def end_session(session_id: str) -> None:
    """Delete the current device's session."""

    client.delete(f"session:{session_id}")


def invalidate_recent_views(user_id: int) -> None:
    """Invalidate cached recent views after a new VIEW event."""

    client.delete(f"recent_views:{user_id}")
