import argparse
import csv
import json
import os
import random
from datetime import datetime, timedelta, timezone


def format_utc(dt):
    """Format datetime as RFC 3339 UTC string with millisecond precision."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def format_mysql_dt(dt):
    """Format datetime for MySQL DATETIME(6)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def generate_dataset(mode="small", seed=42, output_dir="data"):
    rng = random.Random(seed)
    os.makedirs(os.path.join(output_dir, "mysql"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "mongo"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "redis"), exist_ok=True)

    as_of_dt = datetime(2026, 11, 20, 18, 0, 0, tzinfo=timezone.utc)
    start_12m_dt = as_of_dt - timedelta(days=365)

    if mode == "small":
        num_users = 20
        num_products = 15
        num_orders = 40
        num_events = 120
    else:
        num_users = 10000
        num_products = 5000
        num_orders = 100000
        num_events = 500000

    sarah_user_id = 101
    categories = [
        {"category_id": 1, "name": "fashion"},
        {"category_id": 2, "name": "headphones"},
        {"category_id": 3, "name": "home_decor"},
    ]
    shipping_options = [
        {"shipping_option_id": 1, "name": "Standard Shipping", "fee_cents": 500, "dispatch_days": 1, "transit_days": 4, "is_enabled": 1},
        {"shipping_option_id": 2, "name": "Express Shipping", "fee_cents": 1500, "dispatch_days": 1, "transit_days": 2, "is_enabled": 1},
        {"shipping_option_id": 3, "name": "Overnight Shipping", "fee_cents": 2500, "dispatch_days": 0, "transit_days": 1, "is_enabled": 1},
    ]
    devices = ["tablet", "laptop", "phone", "desktop"]

    # 1. Generate Users
    users = []
    for i in range(num_users):
        uid = 100 + i
        created_at = start_12m_dt + timedelta(seconds=rng.randint(0, 86400 * 30))
        if uid == sarah_user_id:
            users.append({"user_id": uid, "name": "Sarah", "email": "sarah@example.com", "created_at": format_mysql_dt(created_at)})
        else:
            users.append({"user_id": uid, "name": f"User_{uid}", "email": f"user_{uid}@example.com", "created_at": format_mysql_dt(created_at)})

    # 2. Generate Products & Variants (MySQL + MongoDB Catalog)
    mysql_products = []
    mysql_variants = []
    mongo_catalog = []
    variant_lookup = {}
    headphone_product_ids = []
    fashion_product_ids = []

    next_variant_id = 10000
    for i in range(num_products):
        pid = 1000 + i
        cat = categories[i % 3]
        cname = cat["name"]
        pname = f"{cname.replace('_', ' ').title()} Item {pid}"
        ret_window = 30
        restock_fee = rng.choice([0, 300, 500])

        mysql_products.append({
            "product_id": pid,
            "category_id": cat["category_id"],
            "name": pname,
            "return_window_days": ret_window,
            "restocking_fee_cents": restock_fee,
        })

        doc_variants = []
        if cname == "fashion":
            fashion_product_ids.append(pid)
            doc_attrs = {"material": rng.choice(["cotton", "wool", "linen", "polyester"])}
            specs = [("blue", "M"), ("aqua-blue", "L"), ("black", "S")]
            for idx, (col, sz) in enumerate(specs):
                vid = next_variant_id
                next_variant_id += 1
                price = rng.randint(2500, 12000)
                stock = 0 if (i % 5 == 0 and idx == 0) else rng.randint(1, 50)
                v_row = {"variant_id": vid, "product_id": pid, "sku": f"SKU-{pid}-{vid}", "unit_price_cents": price, "stock_qty": stock}
                mysql_variants.append(v_row)
                variant_lookup[vid] = v_row
                doc_variants.append({"variant_id": vid, "attributes": {"color": col, "size_code": sz}})
        elif cname == "headphones":
            headphone_product_ids.append(pid)
            doc_attrs = {
                "battery_minutes": rng.choice([600, 1200, 1800]),
                "connectivity": ["Bluetooth 5.3", "USB-C"],
                "weight_g": rng.randint(180, 320),
            }
            for col in ["black", "silver"]:
                vid = next_variant_id
                next_variant_id += 1
                price = rng.randint(8000, 35000)
                stock = rng.randint(2, 40)
                v_row = {"variant_id": vid, "product_id": pid, "sku": f"SKU-{pid}-{vid}", "unit_price_cents": price, "stock_qty": stock}
                mysql_variants.append(v_row)
                variant_lookup[vid] = v_row
                doc_variants.append({"variant_id": vid, "attributes": {"color": col}})
        else:
            doc_attrs = {
                "ceramic_type": rng.choice(["porcelain", "stoneware", "terracotta"]),
                "care_instructions": "Hand wash only",
                "weight_g": rng.randint(400, 2500),
            }
            for h_mm in:
                vid = next_variant_id
                next_variant_id += 1
                price = rng.randint(3000, 15000)
                stock = rng.randint(1, 30)
                v_row = {"variant_id": vid, "product_id": pid, "sku": f"SKU-{pid}-{vid}", "unit_price_cents": price, "stock_qty": stock}
                mysql_variants.append(v_row)
                variant_lookup[vid] = v_row
                doc_variants.append({"variant_id": vid, "attributes": {"width_mm": 120, "height_mm": h_mm, "depth_mm": 120}})

        mongo_catalog.append({
            "product_id": pid,
            "name": pname,
            "category_name": cname,
            "attributes": doc_attrs,
            "variants": doc_variants,
        })

    # 3. Generate Carts, Orders, Order Items, Payments, Returns
    carts = []
    cart_items = []
    orders = []
    order_items = []
    payments = []
    returns = []
    return_refunds = []
    return_exchanges = []

    next_cart_id = 50000
    next_order_id = 200000
    next_oi_id = 500000
    next_pay_id = 800000
    next_ret_id = 900000

    # Ensure Sarah has an ACTIVE cart across devices
    sarah_active_cart_id = next_cart_id
    next_cart_id += 1
    sarah_v1 = mysql_variants[0]["variant_id"]
    carts.append({
        "cart_id": sarah_active_cart_id,
        "user_id": sarah_user_id,
        "status": "ACTIVE",
        "created_at": format_mysql_dt(as_of_dt - timedelta(days=2)),
        "updated_at": format_mysql_dt(as_of_dt - timedelta(hours=1)),
        "last_device": "phone",
        "item_count": 2,
        "version": 2,
    })
    cart_items.append({"cart_id": sarah_active_cart_id, "variant_id": sarah_v1, "quantity": 2})

    # Create Sarah's 6 historical converted orders (including headphones and a return)
    hp_variants = [v for v in mysql_variants if v["product_id"] in headphone_product_ids]
    other_variants = [v for v in mysql_variants if v["product_id"] not in headphone_product_ids]

    for s_idx in range(6):
        cid = next_cart_id
        next_cart_id += 1
        oid = next_order_id
        next_order_id += 1

        placed_dt = start_12m_dt + timedelta(days=35 + s_idx * 45)
        ship_opt = shipping_options[s_idx % 3]
        chosen_vars = [hp_variants[s_idx % len(hp_variants)], other_variants[s_idx % len(other_variants)]]

        carts.append({
            "cart_id": cid,
            "user_id": sarah_user_id,
            "status": "CONVERTED",
            "created_at": format_mysql_dt(placed_dt - timedelta(hours=2)),
            "updated_at": format_mysql_dt(placed_dt),
            "last_device": devices[s_idx % 4],
            "item_count": len(chosen_vars),
            "version": 1,
        })

        subtotal = 0
        created_ois = []
        for v in chosen_vars:
            qty = 1
            subtotal += v["unit_price_cents"] * qty
            cart_items.append({"cart_id": cid, "variant_id": v["variant_id"], "quantity": qty})
            oi_id = next_oi_id
            next_oi_id += 1
            p_meta = mysql_products[v["product_id"] - 1000]
            oi_row = {
                "order_item_id": oi_id,
                "order_id": oid,
                "variant_id": v["variant_id"],
                "quantity": qty,
                "product_name_snapshot": p_meta["name"],
                "unit_price_cents": v["unit_price_cents"],
                "return_window_days": p_meta["return_window_days"],
                "restocking_fee_cents": p_meta["restocking_fee_cents"],
            }
            order_items.append(oi_row)
            created_ois.append(oi_row)

        tax = int(subtotal * 0.08)
        ship_fee = ship_opt["fee_cents"]
        total = subtotal + tax + ship_fee
        est_ship = placed_dt + timedelta(days=ship_opt["dispatch_days"])
        est_deliv = est_ship + timedelta(days=ship_opt["transit_days"])
        status = "CANCELLED" if s_idx == 5 else "DELIVERED"

        orders.append({
            "order_id": oid,
            "cart_id": cid,
            "shipping_option_id": ship_opt["shipping_option_id"],
            "shipping_name_snapshot": ship_opt["name"],
            "status": status,
            "placed_at": format_mysql_dt(placed_dt),
            "subtotal_cents": subtotal,
            "tax_cents": tax,
            "shipping_fee_cents": ship_fee,
            "total_cents": total,
            "estimated_ship_at": format_mysql_dt(est_ship),
            "estimated_delivery_at": format_mysql_dt(est_deliv),
            "shipped_at": format_mysql_dt(est_ship) if status == "DELIVERED" else None,
            "delivered_at": format_mysql_dt(est_deliv) if status == "DELIVERED" else None,
        })

        payments.append({
            "payment_id": next_pay_id,
            "order_id": oid,
            "method": "CARD",
            "provider_token": f"tok_sarah_{oid}",
            "amount_cents": total,
            "approved_at": format_mysql_dt(placed_dt + timedelta(seconds=10)),
        })
        next_pay_id += 1
