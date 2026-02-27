import httpx
import sys
import re

BASE_URL = "http://127.0.0.1:8001"

def test_flow():
    client = httpx.Client(base_url=BASE_URL)
    
    # 1. Register/Login
    print("1. Logging in...")
    login_data = {"username": "testuser", "password": "securepassword123"}
    resp = client.post("/auth/token", data=login_data)
    
    if resp.status_code != 200:
        print(f"Login failed: {resp.text}")
        return False
        
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. Create Post
    print("2. Creating Post...")
    post_data = {
        "body": "Hello World! This is my first signed post.",
        "title": "First Post",
        "visibility": "public"
    }
    resp = client.post("/api/posts", json=post_data, headers=headers)
    
    if resp.status_code != 200:
        print(f"Post creation failed: {resp.text}")
        return False
    
    post_json = resp.json()
    print(f"Post created: ID {post_json['id']}, Signature: {post_json.get('signature')}")
    
    if not post_json.get("signature"):
        print("Error: No signature returned!")
        return False

    # 3. View Timeline (HTML)
    print("3. Fetching Timeline (HTML)...")
    resp = client.get("/")
    if resp.status_code != 200:
        print(f"Timeline fetch failed: {resp.text}")
        return False
        
    if "Hello World!" in resp.text:
        print("Success: Post found in timeline HTML!")
        return True
    else:
        print("Error: Post content not found in timeline.")
        print(resp.text[:500]) # Print beginning of HTML
        return False

if __name__ == "__main__":
    if test_flow():
        print("v2 Post Test PASSED")
        sys.exit(0)
    else:
        print("v2 Post Test FAILED")
        sys.exit(1)
