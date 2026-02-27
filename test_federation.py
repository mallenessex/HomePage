import httpx
import sys
import json

BASE_URL = "http://127.0.0.1:8001"
TEST_USER = "testuser"

def test_webfinger():
    client = httpx.Client(base_url=BASE_URL)
    
    # 1. Test WebFinger
    print("1. Testing WebFinger...")
    resource = f"acct:{TEST_USER}@localhost:8001"
    resp = client.get(f"/.well-known/webfinger?resource={resource}")
    
    if resp.status_code != 200:
        print(f"WebFinger failed: {resp.status_code} - {resp.text}")
        return False
        
    data = resp.json()
    print("WebFinger Response:")
    print(json.dumps(data, indent=2))
    
    # Extract Actor URL
    actor_link = next((link for link in data['links'] if link['rel'] == 'self'), None)
    if not actor_link:
        print("Error: No 'self' link found in WebFinger response")
        return False
        
    actor_url = actor_link['href']
    print(f"Actor URL: {actor_url}")
    
    # 2. Test Actor Endpoint
    print("\n2. Testing Actor Endpoint...")
    # ActivityPub requires Accept header
    headers = {"Accept": "application/activity+json"}
    # Note: Our server might not enforce header strictly yet, but good practice
    
    # We need to strip BASE_URL from actor_url if using client.get with base_url, 
    # but here actor_url is full absolute URL.
    resp = httpx.get(actor_url, headers=headers)
    
    if resp.status_code != 200:
        print(f"Actor fetch failed: {resp.status_code} - {resp.text}")
        return False
        
    actor_data = resp.json()
    print("Actor Response:")
    print(json.dumps(actor_data, indent=2))
    
    # Validation
    if actor_data['preferredUsername'] != TEST_USER:
        print("Error: Usernames don't match")
        return False
        
    if "publicKeyPem" not in actor_data['publicKey']:
        print("Error: No public key PEM found")
        return False
        
    print("\nSUCCESS: WebFinger and Actor profile verified!")
    return True

if __name__ == "__main__":
    if test_webfinger():
        sys.exit(0)
    else:
        sys.exit(1)
