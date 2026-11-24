
import importlib.util

spec = importlib.util.spec_from_file_location(
    "redis_interfaces",
    "redis/interfaces.py",
)

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


# Test 1: Create session
session_id = module.create_session(105, "phone")
print("Created session:", session_id)

# Test 2: Read session
session_data = module.read_session(session_id)
print("Session data:", session_data)

# Test 3: Check TTL
ttl = module.client.ttl(f"session:{session_id}")
print("TTL:", ttl)

# Test 4: End session
module.end_session(session_id)

# Test 5: Confirm deletion
result = module.read_session(session_id)
print("After logout:", result)
