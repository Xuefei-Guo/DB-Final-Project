"""Implement checkout and order operations with explicit transactions."""

from datetime import datetime, timedelta, timezone

from mysql.db import get_connection


def _build_quote(cart, items, tax_cents, shipping_options, shipping_option_id):
    """Calculate a quote from rows already read by the calling transaction."""
    quote_items = []
    for item in items:
        quote_items.append({
            "product_id": item["product_id"],
            "variant_id": item["variant_id"],
            "name": item["name"],
            "sku": item["sku"],
            "quantity": item["quantity"],
            "unit_price_cents": item["unit_price_cents"],
            "stock_qty": item["stock_qty"],
            "line_total_cents": item["quantity"] * item["unit_price_cents"],
        })
    subtotal_cents = sum(item["line_total_cents"] for item in quote_items)
    if subtotal_cents + tax_cents > 2**63 - 1:
        raise ValueError("Checkout amounts exceed the MySQL BIGINT range.")
    shipping_option = None
    if shipping_option_id is not None:
        shipping_option = next(
            (option for option in shipping_options if option["shipping_option_id"] == shipping_option_id),
            None,
        )
        if shipping_option is None:
            raise ValueError("The shipping option does not exist or is disabled.")

    quoted_at = datetime.now(timezone.utc)
    quoted_at = quoted_at.replace(tzinfo=None, microsecond=quoted_at.microsecond // 1000 * 1000)
    shipping_fee_cents = None
    total_cents = None
    estimated_ship_at = None
    estimated_delivery_at = None
    if shipping_option is not None:
        shipping_fee_cents = shipping_option["fee_cents"]
        total_cents = subtotal_cents + tax_cents + shipping_fee_cents
        if total_cents > 2**63 - 1:
            raise ValueError("Checkout amounts exceed the MySQL BIGINT range.")
        estimated_ship_at = quoted_at + timedelta(days=shipping_option["dispatch_days"])
        estimated_delivery_at = estimated_ship_at + timedelta(days=shipping_option["transit_days"])
    return {
        "cart_id": cart["cart_id"],
        "version": cart["version"],
        "items": quote_items,
        "item_count": sum(item["quantity"] for item in quote_items),
        "subtotal_cents": subtotal_cents,
        "tax_cents": tax_cents,
        "shipping_options": shipping_options,
        "shipping_option": shipping_option,
        "shipping_fee_cents": shipping_fee_cents,
        "total_cents": total_cents,
        "quoted_at": quoted_at,
        "estimated_ship_at": estimated_ship_at,
        "estimated_delivery_at": estimated_delivery_at,
    }


def _read_order(cursor, order_id):
    """Read an order with its stored item and approved payment information."""
    cursor.execute("""
        SELECT * FROM orders
        WHERE order_id = %s
        FOR SHARE;
    """, (order_id,))
    order = cursor.fetchone()
    if order is None:
        raise ValueError("The order does not exist.")
    cursor.execute("""
        SELECT
            order_item_id, variant_id, quantity, product_name_snapshot,
            unit_price_cents, return_window_days, restocking_fee_cents
        FROM order_items
        WHERE order_id = %s
        ORDER BY variant_id;
    """, (order_id,))
    order["items"] = list(cursor.fetchall())
    for item in order["items"]:
        # Keep SKU reads current and acquire shared SKU locks in the same ID order.
        cursor.execute("""
            SELECT sku FROM product_variants
            WHERE variant_id = %s
            FOR SHARE;
        """, (item["variant_id"],))
        variant = cursor.fetchone()
        if variant is None:
            raise ValueError("A purchased product variant no longer exists.")
        item["sku"] = variant["sku"]
        item["line_total_cents"] = item["quantity"] * item["unit_price_cents"]
    cursor.execute("""
        SELECT payment_id, method, amount_cents, approved_at
        FROM payments
        WHERE order_id = %s;
    """, (order_id,))
    order["payment"] = cursor.fetchone()
    if order["payment"] is None:
        raise ValueError("The order has no approved payment record.")
    return order


def preview_checkout(
    user_id: int,
    cart_id: int,
    tax_cents: int,
    shipping_option_id: int | None = None,
) -> dict:
    """Quote current cart prices and enabled delivery options without writing data.

    Without a selected delivery option, shipping fees, total, and estimated dates
    are None. Dates are UTC at millisecond precision, without a stored offset.
    Checkout must recheck the quote and stock before placing an order.
    """
    for name, value in (("user_id", user_id), ("cart_id", cart_id)):
        if type(value) is not int or not 1 <= value <= 2**53 - 1:
            raise ValueError(f"{name} must be a positive integer no greater than 2^53 - 1.")
    if type(tax_cents) is not int or not 0 <= tax_cents <= 2**63 - 1:
        raise ValueError("tax_cents must be a nonnegative integer within the MySQL BIGINT range.")
    if shipping_option_id is not None and (
        type(shipping_option_id) is not int or not 1 <= shipping_option_id <= 2**53 - 1
    ):
        raise ValueError("shipping_option_id must be a positive integer no greater than 2^53 - 1.")

    with get_connection() as connection:
        try:
            with connection.cursor() as cursor:
                # Read version, items, prices, and delivery options from one snapshot.
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute("""
                    SELECT cart_id, version, status
                    FROM carts
                    WHERE cart_id = %s AND user_id = %s;
                """, (cart_id, user_id))
                cart = cursor.fetchone()
                if cart is None:
                    raise ValueError("The cart does not exist or does not belong to the user.")
                if cart["status"] != "ACTIVE":
                    raise ValueError("Only an active cart can be checked out.")
                cursor.execute("""
                    SELECT
                        pv.product_id, ci.variant_id, p.name, pv.sku,
                        ci.quantity, pv.unit_price_cents, pv.stock_qty
                    FROM cart_items AS ci
                    INNER JOIN product_variants AS pv ON pv.variant_id = ci.variant_id
                    INNER JOIN products AS p ON p.product_id = pv.product_id
                    WHERE ci.cart_id = %s
                    ORDER BY ci.variant_id;
                """, (cart_id,))
                items = list(cursor.fetchall())
                if not items:
                    raise ValueError("The cart is empty.")
                cursor.execute("""
                    SELECT shipping_option_id, name, fee_cents, dispatch_days, transit_days
                    FROM shipping_options
                    WHERE is_enabled = 1
                    ORDER BY shipping_option_id;
                """)
                shipping_options = list(cursor.fetchall())
                quote = _build_quote(cart, items, tax_cents, shipping_options, shipping_option_id)
            # End the read snapshot before waiting for user confirmation.
            connection.commit()
            return quote
        except Exception:
            connection.rollback()
            raise


def checkout(
    user_id: int,
    cart_id: int,
    confirmed_quote: dict,
    payment_method: str,
    provider_token: str,
) -> dict:
    """Place one order using a selected quote and local simulated payment.

    Return order, cart, quote, and requires_confirmation. Changed cart contents
    or prices require confirmation before any stock or payment writes.
    Existing orders are returned unchanged. Tokens starting with tok_ simulate
    approval, except tok_declined, which simulates a payment failure.
    """
    for name, value in (("user_id", user_id), ("cart_id", cart_id)):
        if type(value) is not int or not 1 <= value <= 2**53 - 1:
            raise ValueError(f"{name} must be a positive integer no greater than 2^53 - 1.")

    with get_connection() as connection:
        try:
            with connection.cursor() as cursor:
                # Serialize checkout and item changes, and verify ownership first.
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

                # A plain read avoids gap-locking an order that does not exist yet.
                cursor.execute("SELECT order_id FROM orders WHERE cart_id = %s", (cart_id,))
                existing_order = cursor.fetchone()
                # All cart writers lock the parent, so these item rows are stable.
                cursor.execute("""
                    SELECT variant_id, quantity FROM cart_items
                    WHERE cart_id = %s ORDER BY variant_id;
                """, (cart_id,))
                cart["items"] = list(cursor.fetchall())
                if existing_order is not None:
                    order = _read_order(cursor, existing_order["order_id"])
                    connection.commit()
                    return {"order": order, "cart": cart, "quote": None, "requires_confirmation": False}
                if cart["status"] != "ACTIVE":
                    raise ValueError("Only an active cart can be checked out.")

                if not isinstance(confirmed_quote, dict):
                    raise ValueError("confirmed_quote must be the selected preview result.")
                if type(confirmed_quote.get("cart_id")) is not int or confirmed_quote["cart_id"] != cart_id:
                    raise ValueError("The confirmed quote belongs to a different cart.")
                expected_version = confirmed_quote.get("version")
                if type(expected_version) is not int or expected_version < 1:
                    raise ValueError("The confirmed quote must contain a positive version.")
                if cart["version"] != expected_version:
                    connection.rollback()
                    return {"order": None, "cart": cart, "quote": None, "requires_confirmation": True}
                if not cart["items"]:
                    raise ValueError("The cart is empty.")

                selected_shipping = confirmed_quote.get("shipping_option")
                if not isinstance(selected_shipping, dict):
                    raise ValueError("Choose a shipping option before checking out.")
                shipping_option_id = selected_shipping.get("shipping_option_id")
                if type(shipping_option_id) is not int or not 1 <= shipping_option_id <= 2**53 - 1:
                    raise ValueError("The confirmed shipping option ID is invalid.")
                money_fields = ("subtotal_cents", "tax_cents", "shipping_fee_cents", "total_cents")
                for field in money_fields:
                    value = confirmed_quote.get(field)
                    if type(value) is not int or not 0 <= value <= 2**63 - 1:
                        raise ValueError(f"{field} must be a nonnegative integer within the MySQL BIGINT range.")
                if confirmed_quote["total_cents"] != (
                    confirmed_quote["subtotal_cents"] + confirmed_quote["tax_cents"]
                    + confirmed_quote["shipping_fee_cents"]
                ):
                    raise ValueError("The confirmed quote amounts do not add up.")
                confirmed_items = confirmed_quote.get("items")
                if not isinstance(confirmed_items, list) or not confirmed_items:
                    raise ValueError("The confirmed quote must include its item lines.")
                confirmed_lines = []
                for item in confirmed_items:
                    if not isinstance(item, dict):
                        raise ValueError("Each confirmed item must be a dictionary.")
                    for field, minimum, maximum in (
                        ("variant_id", 1, 2**53 - 1),
                        ("quantity", 1, 2**31 - 1),
                        ("unit_price_cents", 0, 2**63 - 1),
                    ):
                        value = item.get(field)
                        if type(value) is not int or not minimum <= value <= maximum:
                            raise ValueError(f"The confirmed item {field} is invalid.")
                    confirmed_lines.append((item["variant_id"], item["quantity"], item["unit_price_cents"]))
                if payment_method not in ("CARD", "BANK_ACCOUNT"):
                    raise ValueError("payment_method must be CARD or BANK_ACCOUNT.")
                if not isinstance(provider_token, str) or not 4 < len(provider_token) <= 160 or not provider_token.startswith("tok_"):
                    raise ValueError("Use a simulated provider token starting with tok_, up to 160 characters.")

                # Lock individual SKU rows in ascending ID order, using current reads.
                items = []
                for line in cart["items"]:
                    cursor.execute("""
                        SELECT variant_id, product_id, sku, unit_price_cents, stock_qty
                        FROM product_variants WHERE variant_id = %s
                        FOR UPDATE;
                    """, (line["variant_id"],))
                    variant = cursor.fetchone()
                    if variant is None:
                        raise ValueError("A product variant no longer exists.")
                    # A shared product lock preserves the name and return-policy snapshot.
                    cursor.execute("""
                        SELECT name, return_window_days, restocking_fee_cents
                        FROM products WHERE product_id = %s
                        FOR SHARE;
                    """, (variant["product_id"],))
                    product = cursor.fetchone()
                    if product is None:
                        raise ValueError("A product no longer exists.")
                    items.append({**variant, **product, "quantity": line["quantity"]})
                cursor.execute("""
                    SELECT shipping_option_id, name, fee_cents, dispatch_days, transit_days
                    FROM shipping_options WHERE is_enabled = 1
                    ORDER BY shipping_option_id
                    FOR SHARE;
                """)
                shipping_options = list(cursor.fetchall())
                shipping_available = any(
                    option["shipping_option_id"] == shipping_option_id for option in shipping_options
                )
                quote = _build_quote(
                    cart, items, confirmed_quote["tax_cents"], shipping_options,
                    shipping_option_id if shipping_available else None,
                )
                current_lines = sorted(
                    (item["variant_id"], item["quantity"], item["unit_price_cents"]) for item in items
                )
                shipping_fields = ("shipping_option_id", "name", "fee_cents", "dispatch_days", "transit_days")
                if (
                    not shipping_available
                    or sorted(confirmed_lines) != current_lines
                    or any(confirmed_quote[field] != quote[field] for field in money_fields)
                    or any(selected_shipping.get(field) != quote["shipping_option"].get(field) for field in shipping_fields)
                ):
                    connection.rollback()
                    return {"order": None, "cart": cart, "quote": quote, "requires_confirmation": True}

                for item in items:
                    cursor.execute("""
                        UPDATE product_variants
                        SET stock_qty = stock_qty - %s
                        WHERE variant_id = %s AND stock_qty >= %s;
                    """, (item["quantity"], item["variant_id"], item["quantity"]))
                    if cursor.rowcount != 1:
                        raise ValueError(f"Insufficient stock for SKU {item['sku']}.")
                # Local simulation only: a decline rolls back the preceding stock changes.
                if provider_token == "tok_declined":
                    raise ValueError("The simulated payment was declined.")

                placed_at = quote["quoted_at"]
                shipping = quote["shipping_option"]
                cursor.execute("""
                    INSERT INTO orders (
                        cart_id, shipping_option_id, shipping_name_snapshot, status,
                        placed_at, subtotal_cents, tax_cents, shipping_fee_cents,
                        total_cents, estimated_ship_at, estimated_delivery_at
                    ) VALUES (%s, %s, %s, 'CONFIRMED', %s, %s, %s, %s, %s, %s, %s);
                """, (
                    cart_id, shipping_option_id, shipping["name"], placed_at,
                    quote["subtotal_cents"], quote["tax_cents"], quote["shipping_fee_cents"],
                    quote["total_cents"], quote["estimated_ship_at"], quote["estimated_delivery_at"],
                ))
                order_id = cursor.lastrowid
                if not 1 <= order_id <= 2**53 - 1:
                    raise ValueError("The generated order_id exceeds the shared project ID range.")
                for item in items:
                    cursor.execute("""
                        INSERT INTO order_items (
                            order_id, variant_id, quantity, product_name_snapshot,
                            unit_price_cents, return_window_days, restocking_fee_cents
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """, (
                        order_id, item["variant_id"], item["quantity"], item["name"],
                        item["unit_price_cents"], item["return_window_days"], item["restocking_fee_cents"],
                    ))
                    if not 1 <= cursor.lastrowid <= 2**53 - 1:
                        raise ValueError("The generated order_item_id exceeds the shared project ID range.")
                cursor.execute("""
                    INSERT INTO payments (order_id, method, provider_token, amount_cents, approved_at)
                    VALUES (%s, %s, %s, %s, %s);
                """, (order_id, payment_method, provider_token, quote["total_cents"], placed_at))
                if not 1 <= cursor.lastrowid <= 2**53 - 1:
                    raise ValueError("The generated payment_id exceeds the shared project ID range.")

                order = _read_order(cursor, order_id)
                if sum(item["line_total_cents"] for item in order["items"]) != order["subtotal_cents"]:
                    raise ValueError("Order item amounts do not match the order subtotal.")
                if order["payment"]["amount_cents"] != order["total_cents"]:
                    raise ValueError("The approved payment does not match the order total.")
                cursor.execute("""
                    UPDATE carts
                    SET status = 'CONVERTED', item_count = %s,
                        version = version + 1, updated_at = %s
                    WHERE cart_id = %s;
                """, (quote["item_count"], placed_at, cart_id))
                cart.update(
                    status="CONVERTED", item_count=quote["item_count"],
                    version=cart["version"] + 1, updated_at=placed_at,
                )
            connection.commit()
            return {"order": order, "cart": cart, "quote": None, "requires_confirmation": False}
        except Exception:
            connection.rollback()
            raise


def update_order_status(order_id: int, target_status: str) -> dict:
    """Advance an order or cancel a confirmed order in a back-office demo.

    Return the order with its items and payment information. Repeating the
    current status preserves timestamps and never restores stock twice.
    This operation owns its connection and transaction.
    """
    if type(order_id) is not int or not 1 <= order_id <= 2**53 - 1:
        raise ValueError("order_id must be a positive integer no greater than 2^53 - 1.")
    if target_status not in ("CONFIRMED", "SHIPPED", "DELIVERED", "CANCELLED"):
        raise ValueError("target_status must be CONFIRMED, SHIPPED, DELIVERED, or CANCELLED.")

    with get_connection() as connection:
        try:
            with connection.cursor() as cursor:
                # Serialize status changes and cancellation retries for this order.
                cursor.execute("""
                    SELECT order_id, status, placed_at, shipped_at
                    FROM orders WHERE order_id = %s
                    FOR UPDATE;
                """, (order_id,))
                order = cursor.fetchone()
                if order is None:
                    raise ValueError("The order does not exist.")

                if order["status"] != target_status:
                    allowed_transitions = {
                        "CONFIRMED": ("SHIPPED", "CANCELLED"),
                        "SHIPPED": ("DELIVERED",),
                        "DELIVERED": (),
                        "CANCELLED": (),
                    }
                    if target_status not in allowed_transitions[order["status"]]:
                        raise ValueError(
                            f"Cannot change order status from {order['status']} to {target_status}."
                        )

                    if target_status == "CANCELLED":
                        cursor.execute("""
                            SELECT variant_id, quantity FROM order_items
                            WHERE order_id = %s
                            ORDER BY variant_id;
                        """, (order_id,))
                        items = list(cursor.fetchall())
                        if not items:
                            raise ValueError("The order has no items to restore.")
                        # Restore SKUs in checkout's lock order and prevent INT overflow.
                        for item in items:
                            cursor.execute("""
                                UPDATE product_variants
                                SET stock_qty = stock_qty + %s
                                WHERE variant_id = %s AND stock_qty <= %s;
                            """, (
                                item["quantity"], item["variant_id"],
                                2**31 - 1 - item["quantity"],
                            ))
                            if cursor.rowcount != 1:
                                raise ValueError("Cannot restore stock within the allowed range.")
                        # Keep the converted cart and approved payment as history.
                        cursor.execute("""
                            UPDATE orders SET status = 'CANCELLED'
                            WHERE order_id = %s;
                        """, (order_id,))
                    else:
                        now = datetime.now(timezone.utc)
                        now = now.replace(tzinfo=None, microsecond=now.microsecond // 1000 * 1000)
                        if target_status == "SHIPPED":
                            if now < order["placed_at"]:
                                raise ValueError("Shipment time cannot precede the order time.")
                            cursor.execute("""
                                UPDATE orders SET status = 'SHIPPED', shipped_at = %s
                                WHERE order_id = %s;
                            """, (now, order_id))
                        else:
                            shipped_at = order["shipped_at"]
                            if shipped_at is None or shipped_at < order["placed_at"] or now < shipped_at:
                                raise ValueError("Delivery requires a valid earlier shipment time.")
                            cursor.execute("""
                                UPDATE orders SET status = 'DELIVERED', delivered_at = %s
                                WHERE order_id = %s;
                            """, (now, order_id))

                order = _read_order(cursor, order_id)
            connection.commit()
            return order
        except Exception:
            connection.rollback()
            raise
