from pymongo import MongoClient, ASCENDING
from pymongo.errors import CollectionInvalid


def create_collections(db):
    """Create catalog and events collections with strict JSON Schema validation and indexes."""
    # 1. Catalog collection schema
    catalog_validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["product_id", "name", "category_name", "attributes", "variants"],
            "properties": {
                "product_id": {"bsonType": "long"},
                "name": {"bsonType": "string"},
                "category_name": {"bsonType": "string"},
                "attributes": {"bsonType": "object"},
                "variants": {
                    "bsonType": "array",
                    "minItems": 1,
                    "items": {
                        "bsonType": "object",
                        "required": ["variant_id", "attributes"],
                        "properties": {
                            "variant_id": {"bsonType": "long"},
                            "attributes": {"bsonType": "object"},
                        },
                    },
                },
            },
            "allOf": [
                {
                    "if": {"properties": {"category_name": {"const": "fashion"}}},
                    "then": {
                        "properties": {
                            "attributes": {
                                "required": ["material"],
                                "properties": {"material": {"bsonType": "string"}},
                            },
                            "variants": {
                                "items": {
                                    "properties": {
                                        "attributes": {
                                            "anyOf": [
                                                {"maxProperties": 0},
                                                {
                                                    "required": ["color", "size_code"],
                                                    "properties": {
                                                        "color": {"bsonType": "string"},
                                                        "size_code": {"bsonType": "string"},
                                                    },
                                                },
                                            ]
                                        }
                                    }
                                }
                            },
                        }
                    },
                },
                {
                    "if": {"properties": {"category_name": {"const": "headphones"}}},
                    "then": {
                        "properties": {
                            "attributes": {
                                "required": ["battery_minutes", "connectivity", "weight_g"],
                                "properties": {
                                    "battery_minutes": {"bsonType": "int"},
                                    "connectivity": {
                                        "bsonType": "array",
                                        "items": {"bsonType": "string"},
                                    },
                                    "weight_g": {"bsonType": "int"},
                                },
                            },
                            "variants": {
                                "items": {
                                    "properties": {
                                        "attributes": {
                                            "properties": {"color": {"bsonType": "string"}}
                                        }
                                    }
                                }
                            },
                        }
                    },
                },
                {
                    "if": {"properties": {"category_name": {"const": "home_decor"}}},
                    "then": {
                        "properties": {
                            "attributes": {
                                "required": ["ceramic_type", "care_instructions", "weight_g"],
                                "properties": {
                                    "ceramic_type": {"bsonType": "string"},
                                    "care_instructions": {"bsonType": "string"},
                                    "weight_g": {"bsonType": "int"},
                                    "width_mm": {"bsonType": "int", "minimum": 1},
                                    "height_mm": {"bsonType": "int", "minimum": 1},
                                    "depth_mm": {"bsonType": "int", "minimum": 1},
                                },
                            },
                            "variants": {
                                "items": {
                                    "properties": {
                                        "attributes": {
                                            "properties": {
                                                "width_mm": {"bsonType": "int", "minimum": 1},
                                                "height_mm": {"bsonType": "int", "minimum": 1},
                                                "depth_mm": {"bsonType": "int", "minimum": 1},
                                            }
                                        }
                                    }
                                }
                            },
                        }
                    },
                },
            ],
        }
    }

    try:
        db.create_collection("catalog", validator=catalog_validator)
    except CollectionInvalid:
        db.command("collMod", "catalog", validator=catalog_validator)

    # Catalog indexes
    db.catalog.create_index([("product_id", ASCENDING)], unique=True, name="idx_catalog_product_id")
    db.catalog.create_index(
        [("category_name", ASCENDING), ("product_id", ASCENDING)],
        name="idx_catalog_category_product",
    )

    # 2. Events collection schema
    events_validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["user_id", "event_type", "occurred_at", "device"],
            "properties": {
                "user_id": {"bsonType": "long"},
                "event_type": {"enum": ["VIEW", "SEARCH", "CLICK", "CART_ACTION"]},
                "occurred_at": {"bsonType": "date"},
                "device": {"enum": ["tablet", "laptop", "phone", "desktop"]},
                "product_id": {"bsonType": "long"},
                "search_term": {"bsonType": "string"},
                "duration_ms": {"bsonType": ["int", "null"], "minimum": 0},
                "cart_id": {"bsonType": "long"},
                "variant_id": {"bsonType": "long"},
                "cart_action": {"enum": ["ADD", "SET_QUANTITY", "REMOVE"]},
            },
            "allOf": [
                {
                    "if": {"properties": {"event_type": {"enum": ["VIEW", "CLICK"]}}},
                    "then": {"required": ["product_id"]},
                },
                {
                    "if": {"properties": {"event_type": {"const": "SEARCH"}}},
                    "then": {"required": ["search_term"]},
                },
                {
                    "if": {"properties": {"event_type": {"const": "CART_ACTION"}}},
                    "then": {"required": ["cart_id", "variant_id", "cart_action"]},
                },
            ],
        }
    }

    try:
        db.create_collection("events", validator=events_validator)
    except CollectionInvalid:
        db.command("collMod", "events", validator=events_validator)

    # Events indexes for Q2, Q6, and Q5
    db.events.create_index(
        [("user_id", ASCENDING), ("event_type", ASCENDING), ("occurred_at", ASCENDING)],
        name="idx_events_user_type_time",
    )
    db.events.create_index(
        [("event_type", ASCENDING), ("product_id", ASCENDING)],
        name="idx_events_type_product",
    )


if __name__ == "__main__":
    client = MongoClient("mongodb://localhost:27017/")
    db = client["shop_catalog"]
    create_collections(db)
    print("MongoDB collections, validators, and indexes created successfully.")
