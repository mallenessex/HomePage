"""End-to-end test: generate external client seed, join, approve, register."""
import requests, json, warnings, re, sys, uuid
warnings.filterwarnings("ignore")

SERVER = "http://localhost:9001"
CLIENT_ID = uuid.uuid4().hex  # fresh client ID each run

# ── Step 1: Admin login ──
admin = requests.Session()
admin.verify = False
r = admin.post(f"{SERVER}/auth/login", data={"username": "michael", "password": "testpass123"}, allow_redirects=False)
assert r.status_code == 303, f"Admin login failed: {r.status_code}"
print("[OK] Admin logged in")

# ── Step 1a: Get server ID ──
r = admin.get(f"{SERVER}/admin/settings/client-seed")
assert r.ok, f"Failed to get seed: {r.status_code}"
seed = r.json()
TARGET_SERVER_ID = seed["target_server_id"]
print(f"[OK] Server ID: {TARGET_SERVER_ID}")

# ── Step 1b: Set external URL so external seeds work ──
admin.post(f"{SERVER}/admin/settings/update", data={
    "server_name": "House Fantastico",
    "external_join_policy": "conditional",
    "external_server_url": SERVER,
}, allow_redirects=False)
print("[OK] External URL set")

# ── Step 2: Client sends join request (handshake) ──
print("\n=== JOIN REQUEST (HANDSHAKE) ===")
payload = {
    "target_server_id": TARGET_SERVER_ID,
    "peer_server_id": CLIENT_ID,
    "requested_username": "testclient",
    "requested_display_name": "Test Client User",
    "note": "E2E join test",
}
r = requests.post(f"{SERVER}/.well-known/handshake", json=payload)
assert r.ok, f"Handshake failed: {r.status_code} {r.text}"
data = r.json()
assert data.get("ok"), f"Handshake not ok: {data}"
REQUEST_ID = data["request_id"]
print(f"  Request ID: {REQUEST_ID}")
print(f"  Status: {data['status']}")
assert data["status"] == "pending", f"Expected pending, got {data['status']}"
print("[OK] Join request created (pending)")

# ── Step 3: Admin approves the join request ──
print("\n=== APPROVE JOIN REQUEST ===")
r = admin.post(f"{SERVER}/admin/join-requests/{REQUEST_ID}/approve")
print(f"  Approve HTTP status: {r.status_code}")

# Extract credentials from the approval page HTML
un_match = re.search(r'id="alloc-username"[^>]*>([^<]+)', r.text)
pw_match = re.search(r'id="alloc-password"[^>]*>([^<]+)', r.text)
admin_saw_username = un_match.group(1).strip() if un_match else None
admin_saw_password = pw_match.group(1).strip() if pw_match else None
print(f"  Admin sees username: {admin_saw_username}")
print(f"  Admin sees password: {admin_saw_password}")

# Verify the password is NOT the client_id (that was the bug)
assert admin_saw_password != CLIENT_ID, \
    f"BUG: allocated_password equals the client_id! ({admin_saw_password})"
print("[OK] Password is a secure random token (not peer_server_id)")

# ── Step 4: Client-side status check (should return credentials) ──
print("\n=== CLIENT STATUS CHECK ===")
r2 = requests.get(
    f"{SERVER}/.well-known/join-requests/{REQUEST_ID}",
    params={"peer_server_id": CLIENT_ID},
)
data = r2.json()
print(f"  Status: {data.get('status')}")
print(f"  allocated_username: {data.get('allocated_username')}")
print(f"  allocated_password: {data.get('allocated_password')}")
alloc_user = data.get("allocated_username")
alloc_pass = data.get("allocated_password")

assert data["status"] == "approved", f"Expected approved, got {data['status']}"
assert alloc_user, "No allocated_username in response"
assert alloc_pass, "No allocated_password in response"
assert alloc_pass == admin_saw_password, "Password mismatch between admin page and status API"
print("[OK] Client received credentials")

# ── Step 5: Log in using allocated credentials ──
# (The user account was already created during approval — client just needs to log in)
print("\n=== LOGIN WITH CREDENTIALS ===")
r3 = requests.post(
    f"{SERVER}/auth/login",
    data={"username": alloc_user, "password": alloc_pass},
    allow_redirects=False,
)
print(f"  Login HTTP status: {r3.status_code}")
print(f"  Location: {r3.headers.get('Location', '')}")
cookies = dict(r3.cookies)
has_token = "access_token" in cookies
print(f"  Got auth cookie: {has_token}")

if not has_token:
    print(f"  Response body (first 500): {r3.text[:500]}")
    sys.exit(1)

# ── Step 6: Access feed as newly registered user ──
print("\n=== ACCESS FEED ===")
r4 = requests.get(f"{SERVER}/feed", cookies=cookies)
print(f"  Feed HTTP status: {r4.status_code}")

if r4.status_code == 200:
    print("\n" + "=" * 50)
    print("  FULL END-TO-END JOIN FLOW: SUCCESS")
    print("=" * 50)
else:
    print(f"  Feed access failed: {r4.status_code}")
    sys.exit(1)
