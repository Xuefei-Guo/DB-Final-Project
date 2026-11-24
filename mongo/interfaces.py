import calendar
from datetime import datetime, timezone
from bson import Int64


def _parse_utc(as_of):
    """Parse RFC 3339 string or datetime object into UTC-aware datetime."""
    if isinstance(as_of, datetime):
        return as_of.astimezone(timezone.utc) if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc)


def _subtract_six_calendar_months(dt):
    """Subtract 6 calendar months in UTC, clamping to the last day of the target month if needed."""
    year = dt.year
    month = dt.month - 6
    if month <= 0:
        month += 12
        year -= 1
    max_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, max_day)
    return dt.replace(year=year, month=month, day=day)


def recent_views(db, user_id, as_of):
    """
    Read-only interface for C (Q2 cache miss fallback).
    Returns up to 5 distinct viewed products in the past 6 calendar months up to as_of.
    """
    end_dt = _parse_utc(as_of)
    start_dt = _subtract_six_calendar_months(end_dt)

    pipeline = [
        {
            "$match": {
                "user_id": Int64(user_id),
                "event_type": "VIEW",
                "occurred_at": {"$gte": start_dt, "$lte": end_dt},
            }
        },
        {
            "$group": {
                "_id": "$product_id",
                "last_viewed_at": {"$max": "$occurred_at"},
            }
        },
        {"$sort": {"last_viewed_at": -1, "_id": 1}},
        {"$limit": 5},
        {
            "$lookup": {
                "from": "catalog",
                "localField": "_id",
                "foreignField": "product_id",
                "as": "product_info",
            }
        },
        {"$unwind": "$product_info"},
        {
            "$project": {
                "_id": 0,
                "product_id": "$_id",
                "name": "$product_info.name",
                "last_viewed_at": "$last_viewed_at",
            }
        },
    ]

    results = list(db.events.aggregate(pipeline))
    for r in results:
        r["product_id"] = Int64(r["product_id"])
        if r["last_viewed_at"].tzinfo is None:
            r["last_viewed_at"] = r["last_viewed_at"].replace(tzinfo=timezone.utc)
    return results
