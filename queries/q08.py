"""Q8: Retrieve all of Sarah's orders with item, payment, and shipping details."""

from pprint import pprint
import sys

import pymysql

from mysql.db import get_connection


def q08(connection) -> list[dict]:
    """Return every order item for the dataset's user named Sarah.

    Include cancelled orders and use the stored purchase snapshots.
    The caller owns the connection and transaction; this function manages its cursor.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                o.order_id,
                oi.order_item_id,
                oi.product_name_snapshot,
                pv.sku,
                oi.quantity,
                oi.unit_price_cents,
                p.method AS payment_method,
                o.shipping_name_snapshot,
                o.status AS order_status
            FROM orders AS o
            JOIN carts AS c ON c.cart_id = o.cart_id
            JOIN users AS u ON u.user_id = c.user_id
            JOIN order_items AS oi ON oi.order_id = o.order_id
            JOIN product_variants AS pv ON pv.variant_id = oi.variant_id
            JOIN payments AS p ON p.order_id = o.order_id
            WHERE u.name = 'Sarah'
            ORDER BY o.placed_at DESC, o.order_id DESC, oi.order_item_id;
        """)
        return list(cursor.fetchall())


if __name__ == "__main__":
    try:
        with get_connection() as connection:
            results = q08(connection)
        pprint(results, sort_dicts=False)
    except (pymysql.MySQLError, OSError, ValueError, KeyError) as error:
        print(f"Q8 failed: {error}", file=sys.stderr)
        sys.exit(1)
