
"""Manual integration tests for Q2 Redis caching."""

import importlib.util
from datetime import datetime, timezone
from unittest.mock import patch

from pathlib import Path


path = Path("queries/q02.py").resolve()

spec = importlib.util.spec_from_file_location(
    "q02_module", path
)

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


TEST_USER_ID = 90000000001

AS_OF = datetime(
    2026, 11, 23, 18, 0, 0,
    tzinfo=timezone.utc,
)

OTHER_AS_OF = datetime(
    2026, 11, 22, 18, 0, 0,
    tzinfo=timezone.utc,
)

MOCK_VIEWS = [
    {
        "product_id": 101,
        "name": "Wireless Headphones",
        "last_viewed_at": datetime(
            2026, 11, 22, 15, 30,
            tzinfo=timezone.utc,
        ),
    },
    {
        "product_id": 102,
        "name": "Blue T-Shirt",
        "last_viewed_at": datetime(
            2026, 11, 21, 12, 0,
            tzinfo=timezone.utc,
        ),
    },
]

key = f"recent_views:{TEST_USER_ID}"


def mock_recent_views(user_id, as_of):
    assert user_id == TEST_USER_ID
    return MOCK_VIEWS


def main():
    module.client.delete(key)

    try:
        with patch.object(
            module,
            "fetch_recent_views",
            side_effect=mock_recent_views,
        ) as mock_fetch:

            print("\nTEST 1: Cache miss")

            result1 = module.q02(TEST_USER_ID, AS_OF)

            assert len(result1) == 2
            assert result1[0]["product_id"] == "101"
            assert mock_fetch.call_count == 1

            print("PASS: Fetched from mock MongoDB")

            print("\nTEST 2: Cache hit")

            result2 = module.q02(TEST_USER_ID, AS_OF)

            assert result2 == result1
            assert mock_fetch.call_count == 1

            print("PASS: Redis cache hit")

            print("\nTEST 3: Cache TTL")

            ttl = module.client.ttl(key)

            assert 0 < ttl <= 300
            print("PASS: TTL =", ttl)

            print("\nTEST 4: Different as_of")

            module.q02(TEST_USER_ID, OTHER_AS_OF)

            assert mock_fetch.call_count == 2
            print("PASS: Cache refreshed")

            print("\nTEST 5: Empty results")

            module.client.delete(key)

            with patch.object(
                module,
                "fetch_recent_views",
                return_value=[],
            ) as empty_fetch:

                assert module.q02(TEST_USER_ID, AS_OF) == []
                assert module.q02(TEST_USER_ID, AS_OF) == []
                assert empty_fetch.call_count == 1

            print("PASS: Empty array was cached")

            print("\nTEST 6: Cache invalidation")

            assert module.client.exists(key) == 1

            module.client.delete(key)

            assert module.client.exists(key) == 0
            print("PASS: Cache deleted")

        print("\nALL Q2 MOCK TESTS PASSED")

    finally:
        module.client.delete(key)


if __name__ == "__main__":
    main()
