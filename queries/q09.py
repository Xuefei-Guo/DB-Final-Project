"""Q9: Retrieve Sarah's returned items and their refund or exchange status."""

from pprint import pprint
import sys

import pymysql

from mysql.db import get_connection


def q09(connection) -> list[dict]:
    """Return Sarah's return records, current statuses, and amounts in cents.

    Refund amounts can be None for exchanges or pending refunds.
    The caller owns the connection and transaction; this function manages its cursor.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                r.return_id,
                oi.order_item_id,
                oi.product_name_snapshot,
                pv.sku,
                r.quantity,
                r.resolution,
                r.status,
                rf.refund_amount_cents,
                COALESCE(rf.restocking_fee_cents, 0) AS restocking_fee_cents
            FROM returns AS r
            JOIN order_items AS oi ON oi.order_item_id = r.order_item_id
            JOIN orders AS o ON o.order_id = oi.order_id
            JOIN carts AS c ON c.cart_id = o.cart_id
            JOIN users AS u ON u.user_id = c.user_id
            JOIN product_variants AS pv ON pv.variant_id = oi.variant_id
            LEFT JOIN return_refunds AS rf ON rf.return_id = r.return_id
            WHERE u.name = 'Sarah'
            ORDER BY r.requested_at DESC, r.return_id DESC;
        """)
        return list(cursor.fetchall())


if __name__ == "__main__":
    try:
        with get_connection() as connection:
            results = q09(connection)
        pprint(results, sort_dicts=False)
    except (pymysql.MySQLError, OSError, ValueError, KeyError) as error:
        print(f"Q9 failed: {error}", file=sys.stderr)
        sys.exit(1)
