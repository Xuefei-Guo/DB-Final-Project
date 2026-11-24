# Parameters: as_of (RFC 3339 UTC string or datetime)

from datetime import datetime, timezone


def _parse_utc(as_of):
    if isinstance(as_of, datetime):
        return as_of.astimezone(timezone.utc) if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc)


def q05(db, as_of):
    """
    Q5: Product view counts up to as_of.
    Returns every catalog product with product_id, name, and view_count (0 if never viewed),
    sorted by view_count DESC, product_id ASC.
    """
    end_dt = _parse_utc(as_of)

    pipeline = [
        {
            "$lookup": {
                "from": "events",
                "let": {"pid": "$product_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$event_type", "VIEW"]},
                                    {"$eq": ["$product_id", "$$pid"]},
                                    {"$lte": ["$occurred_at", end_dt]},
                                ]
                            }
                        }
                    },
                    {"$count": "cnt"},
                ],
                "as": "view_stats",
            }
        },
        {
            "$project": {
                "_id": 0,
                "product_id": 1,
                "name": 1,
                "view_count": {
                    "$ifNull": [{"$arrayElemAt": ["$view_stats.cnt", 0]}, 0]
                },
            }
        },
        {"$sort": {"view_count": -1, "product_id": 1}},
    ]

    results = list(db.catalog.aggregate(pipeline))
    for r in results:
        r["product_id"] = int(r["product_id"])
        r["view_count"] = int(r["view_count"])
    return results
