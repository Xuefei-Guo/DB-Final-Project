# Parameters: None

def q01(db):
    """
    Q1: Apparel specifications.
    Returns all fashion products with product_id, name, material, and all variants
    (including out-of-stock variants) containing variant_id, color, and size_code.
    """
    cursor = db.catalog.find(
        {"category_name": "fashion"},
        {
            "_id": 0,
            "product_id": 1,
            "name": 1,
            "attributes.material": 1,
            "variants": 1,
        },
    ).sort("product_id", 1)

    results = []
    for doc in cursor:
        formatted_variants = []
        for v in doc.get("variants", []):
            attrs = v.get("attributes", {})
            formatted_variants.append(
                {
                    "variant_id": int(v["variant_id"]),
                    "color": attrs.get("color"),
                    "size_code": attrs.get("size_code"),
                }
            )
        results.append(
            {
                "product_id": int(doc["product_id"]),
                "name": doc["name"],
                "material": doc.get("attributes", {}).get("material"),
                "variants": formatted_variants,
            }
        )
    return results
