"""Q11: Calculate the percentage of recent carts that did not become orders."""

from pprint import pprint
import sys

import pymysql

from mysql.db import get_connection


def q11(connection) -> list[dict]:
    """Count carts created in the past 30 UTC days and their unconverted share.

    Cancelled orders still count as conversions. An empty cohort returns None
    for the percentage. The caller owns the connection and transaction.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                COUNT(*) AS cart_count,
                COUNT(*) - COUNT(o.order_id) AS unconverted_cart_count,
                100.0 * (COUNT(*) - COUNT(o.order_id))
                    / NULLIF(COUNT(*), 0) AS unconverted_pct
            FROM carts AS c
            LEFT JOIN orders AS o
                ON o.cart_id = c.cart_id
                AND o.placed_at <= UTC_TIMESTAMP(6)
            WHERE c.created_at BETWEEN UTC_TIMESTAMP(6) - INTERVAL 30 DAY
                                   AND UTC_TIMESTAMP(6);
        """)
        result = cursor.fetchone()
    if result["unconverted_pct"] is not None:
        result["unconverted_pct"] = float(result["unconverted_pct"])
    return [result]


if __name__ == "__main__":
    try:
        with get_connection() as connection:
            results = q11(connection)
        pprint(results, sort_dicts=False)
    except (pymysql.MySQLError, OSError, ValueError, KeyError) as error:
        print(f"Q11 failed: {error}", file=sys.stderr)
        sys.exit(1)
