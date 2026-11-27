"""Provide read-only MySQL interfaces for the other database modules."""

from pymysql.connections import Connection


def product_catalog(connection: Connection) -> list[dict]:
    """Return product_id, name, and category_name for every product.

    Use a connection from mysql.db.get_connection() for dictionary rows.
    The caller owns the connection and handles any errors.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                p.product_id,
                p.name,
                c.name AS category_name
            FROM products AS p
            INNER JOIN categories AS c
                ON c.category_id = p.category_id
            ORDER BY p.product_id;
        """)
        return list(cursor.fetchall())


def product_variants_by_ids(
    connection: Connection, product_ids: list[int]
) -> list[dict]:
    """Return variant IDs, SKUs, prices, and stock for the supplied product IDs.

    Include variants with zero stock. An empty input returns an empty list.
    """
    if not product_ids:
        return []

    # Only placeholders are inserted into SQL; bind the ID values separately.
    placeholders = ", ".join(["%s"] * len(product_ids))
    sql = f"""
        SELECT
            product_id,
            variant_id,
            sku,
            unit_price_cents,
            stock_qty
        FROM product_variants
        WHERE product_id IN ({placeholders})
        ORDER BY product_id, variant_id;
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, tuple(product_ids))
        return list(cursor.fetchall())


def purchased_order_products(connection: Connection) -> list[dict]:
    """Return paid, non-cancelled order products with variant quantities combined.

    For a graph snapshot, call this and product_catalog with the same connection
    in one REPEATABLE READ transaction.
    """
    with connection.cursor() as cursor:
        # Return the summed quantity as an integer for Neo4j properties.
        cursor.execute("""
            SELECT
                o.order_id,
                pv.product_id,
                CAST(SUM(oi.quantity) AS SIGNED) AS quantity
            FROM orders AS o
            INNER JOIN payments AS pay
                ON pay.order_id = o.order_id
            INNER JOIN order_items AS oi
                ON oi.order_id = o.order_id
            INNER JOIN product_variants AS pv
                ON pv.variant_id = oi.variant_id
            WHERE o.status IN ('CONFIRMED', 'SHIPPED', 'DELIVERED')
            GROUP BY o.order_id, pv.product_id
            ORDER BY o.order_id, pv.product_id;
        """)
        return list(cursor.fetchall())


def active_cart_by_user_id(connection: Connection, user_id: int) -> int | None:
    """Return the user's active cart ID, or None when there is no active cart.

    The unique active-user constraint guarantees at most one matching row.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT cart_id
            FROM carts
            WHERE user_id = %s AND status = 'ACTIVE';
        """, (user_id,))
        row = cursor.fetchone()
        return row["cart_id"] if row is not None else None
