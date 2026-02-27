import httpx
import sys
import json
import base64
import datetime
import uuid
import asyncio
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import sqlite3

BASE_URL = "http://localhost:8001"

def sign_response_placeholder(date_str):
    # This is for mocking a remote server's response signature if needed
    pass

async def test_outbound_delivery():
    client = httpx.AsyncClient(base_url=BASE_URL)
    
    # 1. Setup local 'testuser' and 'remoteuser' (the follower)
    print("1. Preparing users...")
    await client.post("/auth/register", json={"username": "testuser", "password": "password123"})
    await client.post("/auth/register", json={"username": "remoteuser", "password": "password123"})

    # 2. Make 'remoteuser' follow 'testuser'
    print("2. Setting up follower relationship...")
    conn = sqlite3.connect("V2/local.db")
    cursor = conn.cursor()
    
    # Get testuser ID
    cursor.execute("SELECT id FROM users WHERE username = 'testuser'")
    testuser_id = cursor.fetchone()[0]
    
    # Insert follower record (accepted)
    # We'll point the inbox back to our own server's remoteuser inbox for verification
    remote_inbox = f"{BASE_URL}/users/remoteuser/inbox"
    cursor.execute("""
        INSERT OR REPLACE INTO followers (local_user_id, remote_actor_id, remote_inbox_url, status)
        VALUES (?, ?, ?, ?)
    """, (testuser_id, f"{BASE_URL}/users/remoteuser", remote_inbox, "accepted"))
    conn.commit()
    conn.close()

    # 3. testuser creates a post
    print("3. testuser creating a post...")
    login_resp = await client.post("/auth/token", data={"username": "testuser", "password": "password123"})
    if login_resp.status_code != 200:
        print(f"Login failed: {login_resp.status_code} - {login_resp.text}")
        return False
        
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    post_data = {"body": "Federation test post!", "title": "Hello Federation"}
    resp = await client.post("/api/posts", json=post_data, headers=headers)
    print(f"Post creation: {resp.status_code}")

    # 4. Check 'remoteuser' ActivityLog to see if it received the activity
    print("4. Verifying delivery to remoteuser inbox...")
    # Wait a moment for background task
    await asyncio.sleep(2)
    
    conn = sqlite3.connect("V2/local.db")
    cursor = conn.cursor()
    cursor.execute("SELECT activity_type, raw_json FROM activity_logs WHERE actor_id LIKE '%testuser%'")
    logs = cursor.fetchall()
    conn.close()
    
    if logs:
        print(f"SUCCESS: Found {len(logs)} activities delivered to inbox!")
        for activity_type, raw_json in logs:
            print(f"Type: {activity_type}")
            # print(f"Content: {raw_json[:100]}...")
        return True
    else:
        print("FAILED: No activities found in delivery target inbox.")
        return False

if __name__ == "__main__":
    import asyncio
    if asyncio.run(test_outbound_delivery()):
        sys.exit(0)
    else:
        sys.exit(1)
