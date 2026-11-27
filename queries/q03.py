"""Q3: Retrieve low-stock product variants from MySQL."""

from pprint import pprint
import sys

import pymysql

from mysql.db import get_connection


def q03(connection) -> list[dict]:
    """Return variants with fewer than five units, including zero stock.

    The caller owns the connection and transaction; this function manages its cursor.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                product_id,
                variant_id,
                sku,
                stock_qty
            FROM product_variants
            WHERE stock_qty < 5
            ORDER BY stock_qty, variant_id;
        """)
        return list(cursor.fetchall())


if __name__ == "__main__":
    try:
        with get_connection() as connection:
            results = q03(connection)
        pprint(results, sort_dicts=False)
    except (pymysql.MySQLError, OSError, ValueError, KeyError) as error:
        print(f"Q3 failed: {error}", file=sys.stderr)
        sys.exit(1)
