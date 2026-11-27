"""Q7: Retrieve active carts and their current merchandise subtotals."""

from pprint import pprint
import sys

import pymysql

from mysql.db import get_connection


def q07(connection) -> list[dict]:
    """Return each active cart's last device, total units, and subtotal in cents.

    Empty carts are included. Tax and shipping are determined at checkout.
    The caller owns the connection and transaction; this function manages its cursor.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                c.cart_id,
                c.last_device,
                c.item_count,
                COALESCE(
                    SUM(ci.quantity * CAST(pv.unit_price_cents AS DECIMAL(65, 0))),
                    0
                ) AS subtotal_cents
            FROM carts AS c
            LEFT JOIN cart_items AS ci ON ci.cart_id = c.cart_id
            LEFT JOIN product_variants AS pv ON pv.variant_id = ci.variant_id
            WHERE c.status = 'ACTIVE'
            GROUP BY c.cart_id, c.last_device, c.item_count
            ORDER BY c.cart_id;
        """)
        results = list(cursor.fetchall())
    # MySQL SUM returns Decimal; preserve integer cents in the Python result.
    for row in results:
        row["subtotal_cents"] = int(row["subtotal_cents"])
    return results


if __name__ == "__main__":
    try:
        with get_connection() as connection:
            results = q07(connection)
        pprint(results, sort_dicts=False)
    except (pymysql.MySQLError, OSError, ValueError, KeyError) as error:
        print(f"Q7 failed: {error}", file=sys.stderr)
        sys.exit(1)
