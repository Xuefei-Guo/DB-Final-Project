"""Q13: Retrieve each user's order count and days since their last purchase."""

from datetime import timezone
from pprint import pprint
import sys
from zoneinfo import ZoneInfo

import pymysql

from mysql.db import get_connection


def q13(connection) -> list[dict]:
    """Include all users and count all orders, including cancelled orders.

    Recency uses the last approved payment on a non-cancelled order and Chicago
    calendar dates. The caller owns the connection and transaction.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                u.user_id,
                COUNT(o.order_id) AS order_count,
                MAX(CASE WHEN o.status <> 'CANCELLED' THEN p.approved_at END)
                    AS last_purchase_at,
                UTC_TIMESTAMP(6) AS reference_time
            FROM users AS u
            LEFT JOIN carts AS c ON c.user_id = u.user_id
            LEFT JOIN orders AS o ON o.cart_id = c.cart_id
            LEFT JOIN payments AS p ON p.order_id = o.order_id
            GROUP BY u.user_id
            ORDER BY u.user_id;
        """)
        results = list(cursor.fetchall())

    if results:
        local_zone = ZoneInfo("America/Chicago")
        # Every row uses the same database statement time, converted to Chicago.
        current_day = results[0]["reference_time"].replace(tzinfo=timezone.utc).astimezone(local_zone).date()
        for row in results:
            row.pop("reference_time")
            last_purchase = row.pop("last_purchase_at")
            row["days_since_last_purchase"] = None
            if last_purchase is not None:
                last_day = last_purchase.replace(tzinfo=timezone.utc).astimezone(local_zone).date()
                row["days_since_last_purchase"] = (current_day - last_day).days
    return results


if __name__ == "__main__":
    try:
        with get_connection() as connection:
            results = q13(connection)
        pprint(results, sort_dicts=False)
    except (pymysql.MySQLError, OSError, ValueError, KeyError) as error:
        print(f"Q13 failed: {error}", file=sys.stderr)
        sys.exit(1)
