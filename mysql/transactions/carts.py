"""Implement cart operations with explicit commits and rollbacks."""

from datetime import datetime, timezone

from mysql.db import get_connection


def open_cart(user_id: int, device: str) -> dict:
    """Create or restore a user's active cart and record the accessing device.

    MySQL generates the ID when creating a cart. Restoring an existing cart
    preserves its contents, count, and version.
    This operation owns its connection and commits or rolls back its transaction.
    """
    if type(user_id) is not int or not 1 <= user_id <= 2**53 - 1:
        raise ValueError("user_id must be a positive integer no greater than 2^53 - 1.")
    if device not in ("tablet", "laptop", "phone", "desktop"):
        raise ValueError("device must be tablet, laptop, phone, or desktop.")

    with get_connection() as connection:
        try:
            with connection.cursor() as cursor:
                # Serialize first-time cart creation for the same user.
                cursor.execute("""
                    SELECT user_id
                    FROM users
                    WHERE user_id = %s
                    FOR UPDATE;
                """, (user_id,))
                if cursor.fetchone() is None:
                    raise ValueError("The user does not exist.")

                # Lock an existing cart before updating its access information.
                cursor.execute("""
                    SELECT cart_id
                    FROM carts
                    WHERE user_id = %s AND status = 'ACTIVE'
                    FOR UPDATE;
                """, (user_id,))
                active_cart = cursor.fetchone()

                # DATETIME stores UTC without an offset, at shared millisecond precision.
                now = datetime.now(timezone.utc)
                now = now.replace(tzinfo=None, microsecond=now.microsecond // 1000 * 1000)
                if active_cart is None:
                    # MySQL generates cart_id; item_count and version use schema defaults.
                    cursor.execute("""
                        INSERT INTO carts
                            (user_id, status, created_at, updated_at, last_device)
                        VALUES (%s, 'ACTIVE', %s, %s, %s);
                    """, (user_id, now, now, device))
                    cart_id = cursor.lastrowid
                    if not 1 <= cart_id <= 2**53 - 1:
                        raise ValueError("The generated cart_id exceeds the shared project ID range.")
                else:
                    cart_id = active_cart["cart_id"]
                    cursor.execute("""
                        UPDATE carts
                        SET last_device = %s, updated_at = %s
                        WHERE cart_id = %s;
                    """, (device, now, cart_id))

                cursor.execute("""
                    SELECT
                        cart_id, user_id, status, created_at, updated_at,
                        last_device, item_count, version
                    FROM carts
                    WHERE cart_id = %s;
                """, (cart_id,))
                cart = cursor.fetchone()
            connection.commit()
            return cart
        except Exception:
            connection.rollback()
            raise


def set_cart_item_quantity(
    user_id: int,
    cart_id: int,
    variant_id: int,
    quantity: int,
    device: str,
    expected_version: int,
) -> dict:
    """Set a variant's final quantity, or remove it when quantity is zero.

    Return cart, requires_confirmation, and event. A stale version returns the
    current cart without changing it. Unchanged contents produce no event.
    The application may forward a non-None event to MongoDB after this returns,
    supplying one stable event ID that is reused for any delivery retries.
    """
    for name, value in (("user_id", user_id), ("cart_id", cart_id), ("variant_id", variant_id)):
        if type(value) is not int or not 1 <= value <= 2**53 - 1:
            raise ValueError(f"{name} must be a positive integer no greater than 2^53 - 1.")
    if type(quantity) is not int or not 0 <= quantity <= 2**31 - 1:
        raise ValueError("quantity must be an integer between 0 and 2^31 - 1.")
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("expected_version must be a positive integer.")
    if device not in ("tablet", "laptop", "phone", "desktop"):
        raise ValueError("device must be tablet, laptop, phone, or desktop.")

    with get_connection() as connection:
        try:
            with connection.cursor() as cursor:
                # Every cart writer locks the parent before changing its items.
                cursor.execute("""
                    SELECT
                        cart_id, user_id, status, created_at, updated_at,
                        last_device, item_count, version
                    FROM carts
                    WHERE cart_id = %s AND user_id = %s
                    FOR UPDATE;
                """, (cart_id, user_id))
                cart = cursor.fetchone()
                if cart is None:
                    raise ValueError("The cart does not exist or does not belong to the user.")
                if cart["status"] != "ACTIVE":
                    raise ValueError("Only an active cart can be modified.")

                cursor.execute("""
                    SELECT variant_id, quantity
                    FROM cart_items
                    WHERE cart_id = %s
                    ORDER BY variant_id;
                """, (cart_id,))
                cart["items"] = list(cursor.fetchall())
                if cart["version"] != expected_version:
                    # Release locks before the caller asks the user to reconfirm.
                    connection.rollback()
                    return {"cart": cart, "requires_confirmation": True, "event": None}

                cursor.execute("""
                    SELECT product_id
                    FROM product_variants
                    WHERE variant_id = %s
                    FOR SHARE;
                """, (variant_id,))
                variant = cursor.fetchone()
                if variant is None:
                    raise ValueError("The product variant does not exist.")
                old_quantity = next(
                    (item["quantity"] for item in cart["items"] if item["variant_id"] == variant_id),
                    0,
                )
                changed = old_quantity != quantity
                if changed:
                    if quantity == 0:
                        cursor.execute("""
                            DELETE FROM cart_items
                            WHERE cart_id = %s AND variant_id = %s;
                        """, (cart_id, variant_id))
                    elif old_quantity == 0:
                        cursor.execute("""
                            INSERT INTO cart_items (cart_id, variant_id, quantity)
                            VALUES (%s, %s, %s);
                        """, (cart_id, variant_id, quantity))
                    else:
                        cursor.execute("""
                            UPDATE cart_items
                            SET quantity = %s
                            WHERE cart_id = %s AND variant_id = %s;
                        """, (quantity, cart_id, variant_id))

                # Recompute the denormalized count from the authoritative item rows.
                cursor.execute("""
                    SELECT COALESCE(SUM(quantity), 0) AS item_count
                    FROM cart_items
                    WHERE cart_id = %s;
                """, (cart_id,))
                item_count = int(cursor.fetchone()["item_count"])
                now = datetime.now(timezone.utc)
                now = now.replace(tzinfo=None, microsecond=now.microsecond // 1000 * 1000)
                increment = 1 if changed else 0
                cursor.execute("""
                    UPDATE carts
                    SET item_count = %s, version = version + %s,
                        last_device = %s, updated_at = %s
                    WHERE cart_id = %s;
                """, (item_count, increment, device, now, cart_id))
                cart.update(
                    item_count=item_count,
                    version=cart["version"] + increment,
                    last_device=device,
                    updated_at=now,
                )
                cursor.execute("""
                    SELECT variant_id, quantity
                    FROM cart_items
                    WHERE cart_id = %s
                    ORDER BY variant_id;
                """, (cart_id,))
                cart["items"] = list(cursor.fetchall())

                # SQL commits before the application sends this event to MongoDB.
                event = None
                if changed:
                    action = "REMOVE" if quantity == 0 else ("ADD" if old_quantity == 0 else "SET_QUANTITY")
                    event = {
                        "user_id": user_id,
                        "event_type": "CART_ACTION",
                        "occurred_at": now.replace(tzinfo=timezone.utc),
                        "device": device,
                        "product_id": variant["product_id"],
                        "cart_id": cart_id,
                        "variant_id": variant_id,
                        "cart_action": action,
                    }
            connection.commit()
            return {"cart": cart, "requires_confirmation": False, "event": event}
        except Exception:
            connection.rollback()
            raise
