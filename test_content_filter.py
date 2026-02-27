import httpx
import sys
import asyncio

BASE_URL = "http://localhost:8001"

async def test_content_filter():
    client = httpx.AsyncClient(base_url=BASE_URL)
    
    # 1. Try to post something safe
    print("1. Testing safe post...")
    safe_data = {"body": "This is a lovely morning!"}
    resp = await client.post("/posts/", json=safe_data)
    print(f"Safe post response: {resp.status_code}")
    assert resp.status_code == 200 or resp.status_code == 201
    
    # 2. Try to post something with a forbidden word
    print("2. Testing forbidden word 'naughty'...")
    unsafe_data = {"body": "This is a very naughty post."}
    resp = await client.post("/posts/", json=unsafe_data)
    print(f"Unsafe post response: {resp.status_code}")
    print(f"Response detail: {resp.json().get('detail')}")
    assert resp.status_code == 400
    assert "forbidden word" in resp.json().get("detail")
    
    # 3. Try to post something with substring that shouldn't match (e.g. 'rude' in 'prudent')
    print("3. Testing substring 'prudent' (should be safe)...")
    substring_data = {"body": "It is prudent to be careful."}
    resp = await client.post("/posts/", json=substring_data)
    print(f"Substring post response: {resp.status_code}")
    assert resp.status_code == 200 or resp.status_code == 201

    print("SUCCESS: Content filter is working as expected!")
    return True

if __name__ == "__main__":
    try:
        if asyncio.run(test_content_filter()):
            sys.exit(0)
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)
