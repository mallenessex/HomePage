import httpx
import sys
import json
import base64
import datetime
import uuid
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

BASE_URL = "http://localhost:8001"

def sign_request(method, path, headers, private_key_pem, key_id):
    """
    Slightly simplified AP-style HTTP Signature generation.
    """
    headers_to_sign = ["(request-target)", "host", "date"]
    
    # Construct string to sign
    to_sign_parts = []
    for h in headers_to_sign:
        if h == "(request-target)":
            val = f"{method.lower()} {path}"
        else:
            val = headers.get(h)
        to_sign_parts.append(f"{h}: {val}")
    
    to_sign = "\n".join(to_sign_parts).encode()
    
    # Sign
    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    signature = private_key.sign(
        to_sign,
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    
    sig_b64 = base64.b64encode(signature).decode()
    
    # Build Signature header
    header_val = (
        f'keyId="{key_id}",'
        f'algorithm="rsa-sha256",'
        f'headers="{" ".join(headers_to_sign)}",'
        f'signature="{sig_b64}"'
    )
    return header_val

async def test_signed_follow():
    client = httpx.AsyncClient(base_url=BASE_URL)
    
    # 1. Ensure remoteuser and testuser exist
    print("1. Preparing users...")
    await client.post("/auth/register", json={
        "username": "remoteuser",
        "password": "password123"
    })
    await client.post("/auth/register", json={
        "username": "testuser",
        "password": "password123"
    })

    # 2. Get remoteuser's private key from DB (HACK for testing)
    import sqlite3
    conn = sqlite3.connect("V2/local.db")
    cursor = conn.cursor()
    cursor.execute("SELECT rsa_private_key_enc FROM users WHERE username = 'remoteuser'")
    priv_key_pem = cursor.fetchone()[0]
    conn.close()

    # 3. Send Signed Follow Request
    print("2. Sending Signed Follow Request...")
    actor_url = f"{BASE_URL}/users/remoteuser"
    key_id = f"{actor_url}#main-key"
    path = "/users/testuser/inbox"
    
    activity = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{actor_url}/activities/{uuid.uuid4()}",
        "type": "Follow",
        "actor": actor_url,
        "object": f"{BASE_URL}/users/testuser"
    }
    
    date_str = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    headers = {
        "Host": "localhost:8001",
        "Date": date_str,
        "Content-Type": "application/activity+json"
    }
    
    sig_header = sign_request("POST", path, headers, priv_key_pem, key_id)
    headers["Signature"] = sig_header
    
    resp = await client.post(path, json=activity, headers=headers)
    print(f"Response: {resp.status_code} {resp.text}")
    
    if resp.status_code == 202:
        print("SUCCESS: Signed Follow request accepted!")
    else:
        print("FAILED: Signed request was rejected.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_signed_follow())

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_signed_follow())
