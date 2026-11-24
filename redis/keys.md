
# Redis Key Design

## Overview

Redis is used for session management and recent product
view caching in the e-commerce database project.

## 1. Session Storage

Key pattern: `session:{session_id}`

Data type: String (JSON)

TTL: 1800 seconds (30 minutes of inactivity)

Fields:
- `user_id`: Decimal string identifying the user.
- `device`: One of phone, tablet, laptop, desktop.
- `issued_at`: Session creation time in UTC.
- `last_seen_at`: Last valid session access time in UTC.

Example:

```json
{
  "user_id": "105",
  "device": "phone",
  "issued_at": "2026-11-23T18:00:00.000Z",
  "last_seen_at": "2026-11-23T18:00:00.000Z"
}
```

Each device has an independent session ID.

Reading a valid session atomically updates last_seen_at
and resets the TTL to 1800 seconds.

Logging out deletes only the current device's session.

## 2. Recent Views Cache

Key pattern: `recent_views:{user_id}`

Data type: String (JSON)

TTL: 300 seconds (5 minutes)

Fields:
- `as_of`: Query reference time in UTC.
- `items`: Array containing at most five recently viewed products.

Each item contains:
- `product_id`: Decimal string.
- `name`: Product name.
- `last_viewed_at`: Last view timestamp in UTC.

Example:

```json
{
  "as_of": "2026-11-23T18:00:00.000Z",
  "items": [
    {
      "product_id": "101",
      "name": "Wireless Headphones",
      "last_viewed_at": "2026-11-22T15:30:00.000Z"
    }
  ]
}
```

## 3. Cache Policy

Cache hit:
- The Redis key exists.
- The cached as_of matches the requested as_of.
- Return the cached items.

Cache miss:
- Query MongoDB through recent_views(user_id, as_of).
- Store the result in Redis with a 300-second TTL.
- Return the result.

An empty result array is also cached.

Cache invalidation:
- When a new VIEW event is successfully written to MongoDB,
  delete the corresponding recent_views key.
- Clear recent views caches when reloading the dataset.

If cache invalidation fails, stale results may remain
until the cache expires.
