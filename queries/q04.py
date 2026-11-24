# Parameters: None

def q04(db, product_variants_by_ids_fn):
    """
    Q4: In-stock apparel matching target specifications (blue, aqua-blue, or size L).
    Queries MongoDB for matching variant candidates, then calls A's product_variants_by_ids
    to verify stock_qty > 0 on the exact same product_id and variant_id.
    """
    pipeline = [
        {"$match": {"category_name": "fashion"}},
        {"$unwind": "$variants"},
        {
            "$match": {
                "$or": [
                    {"variants.attributes.color": {"$in": ["blue", "aqua-blue"]}},
                    {"variants.attributes.size_code": "L"},
                ]
            }
        },
        {
            "$project": {
                "_id": 0,
                "product_id": "$product_id",
                "variant_id": "$variants.variant_id",
                "name": "$name",
            }
        },
    ]

    candidates = list(db.catalog.aggregate(pipeline))
    if not candidates:
        return []

    candidate_product_ids = list({int(c["product_id"]) for c in candidates})
    mysql_variants = product_variants_by_ids_fn(candidate_product_ids)

    in_stock_pairs = {
        (int(mv["product_id"]), int(mv["variant_id"]))
        for mv in mysql_variants
        if int(mv.get("stock_qty", 0)) > 0
    }

    matched_products = {}
    for c in candidates:
        pid = int(c["product_id"])
        vid = int(c["variant_id"])
        if (pid, vid) in in_stock_pairs and pid not in matched_products:
            matched_products[pid] = {"product_id": pid, "name": c["name"]}

    return sorted(matched_products.values(), key=lambda x: x["product_id"])
