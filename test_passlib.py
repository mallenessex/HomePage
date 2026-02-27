from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

try:
    print("Hashing 'securepassword123' with argon2...")
    h = pwd_context.hash("securepassword123")
    print(f"Hash: {h}")
    
    print("Verifying...")
    print(pwd_context.verify("securepassword123", h))
except Exception as e:
    import traceback
    traceback.print_exc()
