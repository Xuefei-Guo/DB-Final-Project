"""Implement refund and exchange operations with explicit transactions."""

from datetime import datetime, timedelta, timezone

from mysql.db import get_connection


def _read_return(cursor, return_id):
    """Read a return request and its matching refund or exchange detail."""
    cursor.execute("""
        SELECT * FROM returns WHERE return_id = %s
        FOR SHARE;
    """, (return_id,))
    request = cursor.fetchone()
    if request is None:
        raise ValueError("The return request does not exist.")
    request["refund"] = None
    request["exchange"] = None
    if request["resolution"] == "REFUND":
        cursor.execute("""
            SELECT refund_amount_cents, restocking_fee_cents
            FROM return_refunds WHERE return_id = %s
            FOR SHARE;
        """, (return_id,))
        request["refund"] = cursor.fetchone()
        detail = request["refund"]
    else:
        cursor.execute("""
            SELECT exchange_variant_id, shipped_at
            FROM return_exchanges WHERE return_id = %s
            FOR SHARE;
        """, (return_id,))
        request["exchange"] = cursor.fetchone()
        detail = request["exchange"]
    if detail is None:
        raise ValueError("The return request has no matching detail record.")
    return request


def request_return(
    user_id: int,
    order_item_id: int,
    quantity: int,
    resolution: str,
    reason: str,
    exchange_variant_id: int | None = None,
) -> dict:
    """Register a refund or exchange for one delivered order item.

    Use the purchased return-policy snapshot and allow one request per item.
    An identical retry returns the existing request, including its latest status.
    This operation owns its transaction; it does not refund or change stock.
    """
    for name, value in (("user_id", user_id), ("order_item_id", order_item_id)):
        if type(value) is not int or not 1 <= value <= 2**53 - 1:
            raise ValueError(f"{name} must be a positive integer no greater than 2^53 - 1.")
    if type(quantity) is not int or not 1 <= quantity <= 2**31 - 1:
        raise ValueError("quantity must be a positive integer within the INT range.")
    if resolution not in ("REFUND", "EXCHANGE"):
        raise ValueError("resolution must be REFUND or EXCHANGE.")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 255:
        raise ValueError("reason must contain 1 to 255 characters after trimming.")
    reason = reason.strip()
    if resolution == "EXCHANGE":
        if type(exchange_variant_id) is not int or not 1 <= exchange_variant_id <= 2**53 - 1:
            raise ValueError("EXCHANGE requires a valid exchange_variant_id.")
    elif exchange_variant_id is not None:
        raise ValueError("REFUND must not include exchange_variant_id.")

    with get_connection() as connection:
        try:
            with connection.cursor() as cursor:
                # Serialize applications for the same purchased item before reading requests.
                cursor.execute("""
                    SELECT order_id, variant_id, quantity, return_window_days
                    FROM order_items WHERE order_item_id = %s
                    FOR UPDATE;
                """, (order_item_id,))
                item = cursor.fetchone()
                if item is None:
                    raise ValueError("The purchased order item does not exist.")
                cursor.execute("""
                    SELECT o.status, o.delivered_at
                    FROM orders AS o
                    JOIN carts AS c ON c.cart_id = o.cart_id
                    WHERE o.order_id = %s AND c.user_id = %s
                    FOR SHARE;
                """, (item["order_id"], user_id))
                order = cursor.fetchone()
                if order is None:
                    raise ValueError("The purchased order item does not belong to this user.")

                # The first snapshot read follows the item lock, so a waiting retry sees
                # the preceding committed request without locking gaps for new requests.
                cursor.execute("""
                    SELECT return_id FROM returns WHERE order_item_id = %s;
                """, (order_item_id,))
                existing = cursor.fetchone()
                if existing is None:
                    if order["status"] != "DELIVERED":
                        raise ValueError("Only delivered orders can have a return request.")
                    if quantity > item["quantity"]:
                        raise ValueError("The returned quantity exceeds the purchased quantity.")
                    if resolution == "EXCHANGE":
                        if exchange_variant_id == item["variant_id"]:
                            raise ValueError("An exchange requires a different variant of the same product.")
                        product_ids = []
                        # Keep variant identity locks in the same ascending order as checkout.
                        for variant_id in sorted((item["variant_id"], exchange_variant_id)):
                            cursor.execute("""
                                SELECT product_id FROM product_variants
                                WHERE variant_id = %s
                                FOR SHARE;
                            """, (variant_id,))
                            variant = cursor.fetchone()
                            if variant is None:
                                raise ValueError("The exchange variant does not exist.")
                            product_ids.append(variant["product_id"])
                        if product_ids[0] != product_ids[1]:
                            raise ValueError("An exchange requires a different variant of the same product.")

                    now = datetime.now(timezone.utc)
                    now = now.replace(tzinfo=None, microsecond=now.microsecond // 1000 * 1000)
                    delivered_at = order["delivered_at"]
                    # Use elapsed UTC time and the policy stored on the purchased item.
                    if (
                        delivered_at is None or now < delivered_at
                        or now - delivered_at > timedelta(days=item["return_window_days"])
                    ):
                        raise ValueError("The order item is outside its return window.")
                    cursor.execute("""
                        INSERT INTO returns (
                            order_item_id, quantity, resolution, status, reason,
                            requested_at, updated_at
                        ) VALUES (%s, %s, %s, 'REQUESTED', %s, %s, %s);
                    """, (order_item_id, quantity, resolution, reason, now, now))
                    return_id = cursor.lastrowid
                    if not 1 <= return_id <= 2**53 - 1:
                        raise ValueError("The generated return_id exceeds the shared project ID range.")
                    if resolution == "REFUND":
                        # The refund amount remains unknown until the return is completed.
                        cursor.execute("""
                            INSERT INTO return_refunds (return_id) VALUES (%s);
                        """, (return_id,))
                    else:
                        # Replacement stock is checked and deducted when shipping the exchange.
                        cursor.execute("""
                            INSERT INTO return_exchanges (return_id, exchange_variant_id)
                            VALUES (%s, %s);
                        """, (return_id, exchange_variant_id))
                else:
                    return_id = existing["return_id"]

                request = _read_return(cursor, return_id)
                if existing is not None:
                    saved_variant_id = (
                        request["exchange"]["exchange_variant_id"] if request["exchange"] else None
                    )
                    if (request["quantity"], request["resolution"], request["reason"], saved_variant_id) != (
                        quantity, resolution, reason, exchange_variant_id
                    ):
                        raise ValueError("This order item already has a different return request.")
            connection.commit()
            return request
        except Exception:
            connection.rollback()
            raise


def update_return_status(
    return_id: int,
    target_status: str,
    *,
    return_shipping_method: str | None = None,
    return_label_ref: str | None = None,
    return_tracking_code: str | None = None,
    estimated_resolution_at: datetime | None = None,
) -> dict:
    """Record return progress and shipping information in a back-office demo.

    None leaves existing metadata unchanged. Naive datetime input means UTC;
    aware input is converted to UTC. Identical retries do not change updated_at.
    This operation owns its transaction and never refunds or changes stock.
    Use complete_return to move a received request to COMPLETED.
    """
    if type(return_id) is not int or not 1 <= return_id <= 2**53 - 1:
        raise ValueError("return_id must be a positive integer no greater than 2^53 - 1.")
    if target_status not in ("REQUESTED", "IN_TRANSIT", "RECEIVED", "COMPLETED", "REJECTED"):
        raise ValueError("target_status must be a valid return status.")

    updates = {}
    for name, value, limit in (
        ("return_shipping_method", return_shipping_method, 24),
        ("return_label_ref", return_label_ref, 160),
        ("return_tracking_code", return_tracking_code, 64),
    ):
        if value is not None:
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
                raise ValueError(f"{name} must contain 1 to {limit} characters after trimming.")
            updates[name] = value.strip()
    if estimated_resolution_at is not None:
        if not isinstance(estimated_resolution_at, datetime):
            raise ValueError("estimated_resolution_at must be a datetime value.")
        if estimated_resolution_at.utcoffset() is not None:
            estimated_resolution_at = estimated_resolution_at.astimezone(timezone.utc)
        updates["estimated_resolution_at"] = estimated_resolution_at.replace(
            tzinfo=None, microsecond=estimated_resolution_at.microsecond // 1000 * 1000,
        )

    with get_connection() as connection:
        try:
            with connection.cursor() as cursor:
                # Serialize progress changes and retries for the same return request.
                cursor.execute("""
                    SELECT * FROM returns WHERE return_id = %s
                    FOR UPDATE;
                """, (return_id,))
                request = cursor.fetchone()
                if request is None:
                    raise ValueError("The return request does not exist.")
                changed = request["status"] != target_status or any(
                    request[name] != value for name, value in updates.items()
                )
                if changed:
                    if request["status"] in ("COMPLETED", "REJECTED"):
                        raise ValueError("A completed or rejected request cannot be changed.")
                    allowed_transitions = {
                        "REQUESTED": ("IN_TRANSIT", "REJECTED"),
                        "IN_TRANSIT": ("RECEIVED",),
                        "RECEIVED": (),
                    }
                    if request["status"] != target_status:
                        if target_status == "COMPLETED":
                            raise ValueError("Use complete_return to complete a received request.")
                        if target_status not in allowed_transitions[request["status"]]:
                            raise ValueError(
                                f"Cannot change return status from {request['status']} to {target_status}."
                            )
                    metadata = {**request, **updates}
                    if request["status"] != target_status and target_status == "IN_TRANSIT" and (
                        not metadata["return_shipping_method"] or not metadata["return_tracking_code"]
                    ):
                        raise ValueError("An in-transit return requires a shipping method and tracking code.")
                    estimate = metadata["estimated_resolution_at"]
                    if estimate is not None and estimate < request["requested_at"]:
                        raise ValueError("Estimated resolution cannot precede the return request.")
                    now = datetime.now(timezone.utc)
                    now = now.replace(tzinfo=None, microsecond=now.microsecond // 1000 * 1000)
                    if now < max(request["requested_at"], request["updated_at"]):
                        raise ValueError("Progress time cannot precede the stored request or update time.")
                    cursor.execute("""
                        UPDATE returns
                        SET status = %s, return_shipping_method = %s,
                            return_label_ref = %s, return_tracking_code = %s,
                            estimated_resolution_at = %s, updated_at = %s
                        WHERE return_id = %s;
                    """, (
                        target_status, metadata["return_shipping_method"],
                        metadata["return_label_ref"], metadata["return_tracking_code"],
                        estimate, now, return_id,
                    ))
                request = _read_return(cursor, return_id)
            connection.commit()
            return request
        except Exception:
            connection.rollback()
            raise


def complete_return(return_id: int, *, restock_returned: bool) -> dict:
    """Complete a received return with a local refund or replacement shipment.

    The caller supplies an inspection decision: True restores all returned units
    to the original SKU; False leaves them out of sellable stock. A completed
    request always returns its stored result without repeating any side effects.
    This operation owns its connection and transaction. No payment service is called.
    """
    if type(return_id) is not int or not 1 <= return_id <= 2**53 - 1:
        raise ValueError("return_id must be a positive integer no greater than 2^53 - 1.")
    if type(restock_returned) is not bool:
        raise ValueError("restock_returned must be a boolean inspection decision.")

    with get_connection() as connection:
        try:
            with connection.cursor() as cursor:
                # Serialize completion and retries before inspecting monetary or stock data.
                cursor.execute("""
                    SELECT return_id FROM returns WHERE return_id = %s
                    FOR UPDATE;
                """, (return_id,))
                if cursor.fetchone() is None:
                    raise ValueError("The return request does not exist.")
                request = _read_return(cursor, return_id)
                if request["status"] != "COMPLETED":
                    if request["status"] != "RECEIVED":
                        raise ValueError("Only received returns can be completed.")
                    # Purchased item fields are immutable. An ordinary read avoids
                    # reversing request_return's order-item-before-return lock order.
                    cursor.execute("""
                        SELECT variant_id, quantity, unit_price_cents, restocking_fee_cents
                        FROM order_items WHERE order_item_id = %s;
                    """, (request["order_item_id"],))
                    item = cursor.fetchone()
                    if item is None or not 1 <= request["quantity"] <= item["quantity"]:
                        raise ValueError("The returned quantity does not match the purchased item.")
                    quantity = request["quantity"]
                    original_variant_id = item["variant_id"]
                    exchange_variant_id = (
                        request["exchange"]["exchange_variant_id"] if request["exchange"] else None
                    )
                    if exchange_variant_id == original_variant_id:
                        raise ValueError("An exchange requires a different variant of the same product.")

                    variant_ids = set()
                    if restock_returned or exchange_variant_id is not None:
                        variant_ids.add(original_variant_id)
                    if exchange_variant_id is not None:
                        variant_ids.add(exchange_variant_id)
                    variants = {}
                    # Use the same ascending SKU lock order as checkout and cancellation.
                    for variant_id in sorted(variant_ids):
                        cursor.execute("""
                            SELECT product_id FROM product_variants
                            WHERE variant_id = %s
                            FOR UPDATE;
                        """, (variant_id,))
                        variant = cursor.fetchone()
                        if variant is None:
                            raise ValueError("A returned or replacement variant no longer exists.")
                        variants[variant_id] = variant
                    if exchange_variant_id is not None and (
                        variants[original_variant_id]["product_id"]
                        != variants[exchange_variant_id]["product_id"]
                    ):
                        raise ValueError("An exchange requires a different variant of the same product.")

                    now = datetime.now(timezone.utc)
                    now = now.replace(tzinfo=None, microsecond=now.microsecond // 1000 * 1000)
                    if now < max(request["requested_at"], request["updated_at"]):
                        raise ValueError("Completion time cannot precede the stored request or update time.")
                    if exchange_variant_id is not None:
                        cursor.execute("""
                            UPDATE product_variants SET stock_qty = stock_qty - %s
                            WHERE variant_id = %s AND stock_qty >= %s;
                        """, (quantity, exchange_variant_id, quantity))
                        if cursor.rowcount != 1:
                            raise ValueError("Insufficient stock for the replacement variant.")
                        cursor.execute("""
                            UPDATE return_exchanges SET shipped_at = %s
                            WHERE return_id = %s;
                        """, (now, return_id))
                    else:
                        gross_amount = quantity * item["unit_price_cents"]
                        if not 0 <= gross_amount <= 2**63 - 1:
                            raise ValueError("The returned item amount exceeds the BIGINT range.")
                        # The policy charges one fixed fee per returned order item,
                        # capped at its returned merchandise amount, not once per unit.
                        fee = min(item["restocking_fee_cents"], gross_amount)
                        cursor.execute("""
                            UPDATE return_refunds
                            SET refund_amount_cents = %s, restocking_fee_cents = %s
                            WHERE return_id = %s;
                        """, (gross_amount - fee, fee, return_id))
                    if restock_returned:
                        cursor.execute("""
                            UPDATE product_variants SET stock_qty = stock_qty + %s
                            WHERE variant_id = %s AND stock_qty <= %s;
                        """, (quantity, original_variant_id, 2**31 - 1 - quantity))
                        if cursor.rowcount != 1:
                            raise ValueError("Cannot restore returned stock within the INT range.")
                    cursor.execute("""
                        UPDATE returns SET status = 'COMPLETED', updated_at = %s
                        WHERE return_id = %s;
                    """, (now, return_id))
                    request = _read_return(cursor, return_id)
            connection.commit()
            return request
        except Exception:
            connection.rollback()
            raise
