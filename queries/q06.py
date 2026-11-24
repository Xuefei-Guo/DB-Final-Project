# Parameters: sarah_user_id (int), as_of (RFC 3339 UTC string or datetime)

from datetime import datetime, timedelta, timezone
from bson import Int64


def _parse_utc(as_of):
    if isinstance(as_of, datetime):
        return as_of.astimezone(timezone.utc) if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc)


def q06(db, sarah_user_id, as_of):
    """
    Q6: Search term frequency and local time-of-day distribution (America/Chicago)
    for Sarah over the last 30 UTC days up to as_of.
    """
    end_dt = _parse_utc(as_of)
    start_dt = end_dt - timedelta(days=30)

    pipeline = [
        {
            "$match": {
                "user_id": Int64(sarah_user_id),
                "event_type": "SEARCH",
                "occurred_at": {"$gte": start_dt, "$lte": end_dt},
            }
        },
        {
            "$project": {
                "normalized_term": {"$toLower": {"$trim": {"input": "$search_term"}}},
                "local_hour": {
                    "$hour": {"date": "$occurred_at", "timezone": "America/Chicago"}
                },
            }
        },
        {
            "$group": {
                "_id": "$normalized_term",
                "total_count": {"$sum": 1},
                "night_count": {
                    "$sum": {
                        "$cond": [
                            {"$and": [{"$gte": ["$local_hour", 0]}, {"$lt": ["$local_hour", 6]}]},
                            1,
                            0,
                        ]
                    }
                },
                "morning_count": {
                    "$sum": {
                        "$cond": [
                            {"$and": [{"$gte": ["$local_hour", 6]}, {"$lt": ["$local_hour", 12]}]},
                            1,
                            0,
                        ]
                    }
                },
                "afternoon_count": {
                    "$sum": {
                        "$cond": [
                            {"$and": [{"$gte": ["$local_hour", 12]}, {"$lt": ["$local_hour", 18]}]},
                            1,
                            0,
                        ]
                    }
                },
                "evening_count": {
                    "$sum": {
                        "$cond": [
                            {"$and": [{"$gte": ["$local_hour", 18]}, {"$lt": ["$local_hour", 24]}]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "search_term": "$_id",
                "total_count": 1,
                "night_count": 1,
                "morning_count": 1,
                "afternoon_count": 1,
                "evening_count": 1,
            }
        },
        {"$sort": {"total_count": -1, "search_term": 1}},
    ]

    return list(db.events.aggregate(pipeline))
