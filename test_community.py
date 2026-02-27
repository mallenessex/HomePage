import httpx
import sys
import asyncio

BASE_URL = "http://localhost:8001"

async def test_follow_flow():
    client = httpx.AsyncClient(base_url=BASE_URL)
    
    # 1. We need a handle. Since we are on localhost, let's try to follow our local 'testuser'
    # Actually, the logic in community/follow handles both.
    
    print("1. Testing Discovery (WebFinger)...")
    handle = "testuser@127.0.0.1:8001"
    
    form_data = {"handle": handle}
    # Note: community/follow normally requires a logged in user in the DB.
    # Our current implementation picks the first user in the DB as 'current_user' for demo.
    
    resp = await client.post("/community/follow", data=form_data)
    print(f"Follow Response: {resp.status_code}")
    # print(resp.text)
    
    if "Successfully sent follow request" in resp.text:
        print("SUCCESS: Follow flow initiated and signature sent!")
        return True
    else:
        print("FAILED: Follow flow did not complete as expected.")
        return False

if __name__ == "__main__":
    if asyncio.run(test_follow_flow()):
        sys.exit(0)
    else:
        sys.exit(1)
