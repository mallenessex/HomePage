import httpx
import sys

BASE_URL = "http://127.0.0.1:8001"

def test_register():
    print(f"Testing Registration on {BASE_URL}...")
    user_data = {
        "username": "testuser",
        "password": "securepassword123",
        "display_name": "Test User"
    }
    client = httpx.Client()
    try:
        response = client.post(f"{BASE_URL}/auth/register", json=user_data)
        if response.status_code == 200:
            print("User registered successfully!")
            print(response.json())
            return True
        elif response.status_code == 400 and "already registered" in response.text:
             print("User already registered (expected on re-run).")
             return True
        else:
            print(f"Registration failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Error connecting to server: {e}")
        return False
    finally:
        client.close()

def test_login():
    print("Testing Login...")
    login_data = {
        "username": "testuser",
        "password": "securepassword123"
    }
    client = httpx.Client()
    response = client.post(f"{BASE_URL}/auth/token", data=login_data)
    client.close()
    
    if response.status_code == 200:
        token = response.json().get("access_token")
        print("Login successful! Token received.")
        return token
    else:
        print(f"Login failed: {response.status_code} - {response.text}")
        return None

def test_me(token):
    print("Testing /auth/me...")
    headers = {"Authorization": f"Bearer {token}"}
    client = httpx.Client()
    response = client.get(f"{BASE_URL}/auth/me", headers=headers)
    client.close()
    
    if response.status_code == 200:
        print("User details retrieved successfully!")
        print(response.json())
        return True
    else:
        print(f"Failed to get user details: {response.status_code} - {response.text}")
        return False

if __name__ == "__main__":
    if test_register():
        token = test_login()
        if token:
           test_me(token)
    else:
        sys.exit(1)
