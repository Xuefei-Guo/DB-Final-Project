import json
from bson import Int64, ObjectId


def sync_products(db, mysql_products, new_product_specs=None):
    """
    Sync product_id, name, and category_name from A's product_catalog.
    New products require initial attributes and variants; existing ones only update name/category_name.
    """
    new_product_specs = new_product_specs or {}
    for item in mysql_products:
        pid = Int64(item["product_id"])
        existing = db.catalog.find_one({"product_id": pid})
        if existing:
            db.catalog.update_one(
                {"product_id": pid},
                {"$set": {"name": item["name"], "category_name": item["category_name"]}},
            )
        else:
            spec = new_product_specs.get(int(pid))
            if not spec:
                raise ValueError(f"Missing initial attributes/variants for new product_id={pid}")
            variants = [
                {"variant_id": Int64(v["variant_id"]), "attributes": v["attributes"]}
                for v in spec["variants"]
            ]
            _validate_unique_variants(variants)
            db.catalog.insert_one(
                {
                    "product_id": pid,
                    "name": item["name"],
                    "category_name": item["category_name"],
                    "attributes": spec["attributes"],
                    "variants": variants,
                }
            )


def _validate_unique_variants(variants):
    """Ensure variant_id and attribute combinations are unique within a single product."""
    seen_ids = set()
    seen_attrs = set()
    for v in variants:
        vid = int(v["variant_id"])
        if vid in seen_ids:
            raise ValueError(f"Duplicate variant_id={vid} within the same product.")
        seen_ids.add(vid)

        attr_sig = json.dumps(v["attributes"], sort_keys=True)
        if attr_sig in seen_attrs:
            raise ValueError(f"Duplicate variant attributes {attr_sig} within the same product.")
        seen_attrs.add(attr_sig)


def write_variant_specs(db, product_id, variants_input, mysql_variants):
    """
    Verify variant IDs match A's product_variants_by_ids(product_ids) and update variant specs.
    """
    pid = Int64(product_id)
    mysql_vids = {int(v["variant_id"]) for v in mysql_variants if int(v["product_id"]) == int(pid)}
    input_vids = {int(v["variant_id"]) for v in variants_input}

    if mysql_vids != input_vids:
        raise ValueError(
            f"Variant ID mismatch for product_id={pid}: MySQL={mysql_vids}, Input={input_vids}"
        )

    formatted_variants = [
        {"variant_id": Int64(v["variant_id"]), "attributes": v["attributes"]}
        for v in variants_input
    ]
    _validate_unique_variants(formatted_variants)

    db.catalog.update_one(
        {"product_id": pid},
        {"$set": {"variants": formatted_variants}},
    )


def record_event(db, event_doc, invalidate_recent_views_fn=None):
    """
    Idempotent event logging using pre-generated _id.
    Triggers C's invalidate_recent_views(user_id) after a new VIEW event is saved.
    """
    doc = dict(event_doc)
    event_id = doc.get("_id") or ObjectId()
    doc["_id"] = event_id
    doc["user_id"] = Int64(doc["user_id"])

    if "product_id" in doc and doc["product_id"] is not None:
        doc["product_id"] = Int64(doc["product_id"])
    if "cart_id" in doc and doc["cart_id"] is not None:
        doc["cart_id"] = Int64(doc["cart_id"])
    if "variant_id" in doc and doc["variant_id"] is not None:
        doc["variant_id"] = Int64(doc["variant_id"])
    if doc["event_type"] == "SEARCH" and "search_term" in doc:
        doc["search_term"] = doc["search_term"].strip().lower()

    result = db.events.update_one({"_id": event_id}, {"$setOnInsert": doc}, upsert=True)

    if result.upserted_id is not None and doc["event_type"] == "VIEW":
        if invalidate_recent_views_fn is not None:
            invalidate_recent_views_fn(int(doc["user_id"]))

    return event_id


def update_view_duration(db, event_id, duration_ms):
    """Update duration_ms on an existing VIEW event when the user leaves the product page."""
    if duration_ms < 0:
        raise ValueError("duration_ms must be >= 0")
    db.events.update_one(
        {"_id": ObjectId(event_id), "event_type": "VIEW"},
        {"$set": {"duration_ms": int(duration_ms)}},
    )
