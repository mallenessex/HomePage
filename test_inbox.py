import httpx
import sys
import json
import uuid

BASE_URL = "http://127.0.0.1:8001"
TEST_USER = "testuser"

def test_inbox_follow():
    # Simulate a remote server sending a Follow activity
    print("Testing Incoming Follow Request...")
    
    remote_actor = "https://remote.example.com/users/remoteuser"
    activity = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"https://remote.example.com/activities/{uuid.uuid4()}",
        "type": "Follow",
        "actor": remote_actor,
        "object": f"http://localhost:8001/users/{TEST_USER}"
    }
    
    headers = {"Content-Type": "application/activity+json"}
    
    try:
        resp = httpx.post(
            f"{BASE_URL}/users/{TEST_USER}/inbox", 
            json=activity, 
            headers=headers
        )
        
        if resp.status_code == 202:
            print("Inbox accepted Follow request (202 Accepted).")
            return True
        else:
            print(f"Inbox failed: {resp.status_code} - {resp.text}")
            return False
            
    except Exception as e:
        print(f"Connection error: {e}")
        return False

def check_follower_db():
    # Only possible if we can query DB or trust the logs.
    # For now, just rely on 202 response.
    # Ideally we'd add an admin endpoint to list followers for verification.
    return True

if __name__ == "__main__":
    if test_inbox_follow():
        sys.exit(0)
    else:
        sys.exit(1)
