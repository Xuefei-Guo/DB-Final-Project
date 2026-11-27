"""Q10: Calculate Sarah's average number of days between purchases."""

from datetime import timezone
from pprint import pprint
import sys
from zoneinfo import ZoneInfo

import pymysql

from mysql.db import get_connection


def q10(connection) -> list[dict]:
    """Average the Chicago calendar-day gaps between Sarah's paid orders.

    Cancelled orders are excluded. Fewer than two purchases returns None.
    The caller owns the connection and transaction; this function manages its cursor.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                COUNT(*) AS purchase_count,
                MIN(p.approved_at) AS first_purchase_at,
                MAX(p.approved_at) AS last_purchase_at
            FROM payments AS p
            JOIN orders AS o ON o.order_id = p.order_id
            JOIN carts AS c ON c.cart_id = o.cart_id
            JOIN users AS u ON u.user_id = c.user_id
            WHERE u.name = 'Sarah' AND o.status <> 'CANCELLED';
        """)
        purchases = cursor.fetchone()

    average_days = None
    if purchases["purchase_count"] >= 2:
        # Convert stored UTC times without requiring MySQL timezone tables.
        local_zone = ZoneInfo("America/Chicago")
        first_day = purchases["first_purchase_at"].replace(tzinfo=timezone.utc).astimezone(local_zone).date()
        last_day = purchases["last_purchase_at"].replace(tzinfo=timezone.utc).astimezone(local_zone).date()
        # Adjacent day gaps sum to the last date minus the first date.
        average_days = (last_day - first_day).days / (purchases["purchase_count"] - 1)
    return [{"average_days_between_purchases": average_days}]


if __name__ == "__main__":
    try:
        with get_connection() as connection:
            results = q10(connection)
        pprint(results, sort_dicts=False)
    except (pymysql.MySQLError, OSError, ValueError, KeyError) as error:
        print(f"Q10 failed: {error}", file=sys.stderr)
        sys.exit(1)
