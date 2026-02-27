import httpx
import sys
import asyncio

BASE_URL = "http://localhost:8001"

async def test_profile_update():
    client = httpx.AsyncClient(base_url=BASE_URL)
    
    print("1. Updating profile for testuser...")
    # Since we lack real auth in this test, we rely on the demo-mode in the router
    # which picks 'testuser' if it exists.
    
    update_data = {
        "display_name": "Mike Allen",
        "bio": "Building a better, decentralized family internet.",
        "avatar_url": "https://api.dicebear.com/7.x/avataaars/svg?seed=Mike"
    }
    
    resp = await client.post("/users/settings/profile", data=update_data, follow_redirects=True)
    print(f"Update response: {resp.status_code}")
    
    print("2. Verifying Actor JSON profile...")
    resp = await client.get("/users/testuser", headers={"Accept": "application/activity+json"})
    if resp.status_code == 200:
        data = resp.json()
        print(f"Actor Name: {data.get('name')}")
        print(f"Actor Summary: {data.get('summary')}")
        print(f"Actor Icon: {data.get('icon')}")
        
        if data.get('name') == "Mike Allen" and "decentralized" in data.get('summary'):
            print("SUCCESS: Profile updated and reflected in ActivityPub Actor!")
            return True
    
    print("FAILED: Profile not reflected.")
    return False

if __name__ == "__main__":
    if asyncio.run(test_profile_update()):
        sys.exit(0)
    else:
        sys.exit(1)
