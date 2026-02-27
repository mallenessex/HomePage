import httpx
import sys

BASE_URL = "http://127.0.0.1:8001"

def test_media_upload():
    client = httpx.Client(base_url=BASE_URL)
    
    # 1. Login
    print("1. Logging in...")
    login_data = {"username": "testuser", "password": "securepassword123"}
    resp = client.post("/auth/token", data=login_data)
    
    if resp.status_code != 200:
        print(f"Login failed: {resp.text}")
        return False
        
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # 2. Upload File (Create a dummy file first)
    print("2. Uploading Media...")
    files = {'file': ('test_image.png', b'fake image content', 'image/png')}
    
    resp = client.post("/api/media/upload", files=files, headers=headers)
    
    if resp.status_code != 200:
        print(f"Upload failed: {resp.text}")
        return False
    
    data = resp.json()
    print(f"Upload success: {data}")
    
    # 3. Verify Access
    print("3. Verifying Media Access...")
    media_url = data['url']
    resp = client.get(media_url)
    
    if resp.status_code == 200 and resp.content == b'fake image content':
        print("Success: Media accessible!")
        return True
    else:
        print(f"Media access failed: {resp.status_code}")
        return False

if __name__ == "__main__":
    if test_media_upload():
        sys.exit(0)
    else:
        sys.exit(1)
