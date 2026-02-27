from passlib.context import CryptContext
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from .config import settings
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography import exceptions as errors

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# --- Password Hashing ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# --- JWT Tokens ---
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

# --- Key Generation (Ed25519) ---
def generate_keypair():
    """
    Generates an Ed25519 keypair.
    Returns (public_key_hex, private_key_hex).
    """
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Serialize private key to bytes
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )

    # Serialize public key to bytes
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    
    return public_bytes.hex(), private_bytes.hex()

# --- Signing ---
def sign_message(private_key_hex: str, message: bytes) -> str:
    """
    Signs a message using the Ed25519 private key.
    """
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    signature = private_key.sign(message)
    return signature.hex()

def verify_signature(public_key_hex: str, message: bytes, signature_hex: str) -> bool:
    """
    Verifies a signature using the Ed25519 public key.
    """
    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(bytes.fromhex(signature_hex), message)
        return True
    except errors.InvalidSignature:  # Assuming InvalidSignature imported from cryptography.exceptions
        return False
    except Exception:
        return False

def get_public_key_pem(public_key_hex: str) -> str:
    """
    Converts a hex-encoded Ed25519 public key to PEM format.
    """
    public_bytes = bytes.fromhex(public_key_hex)
    public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_bytes)
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return pem.decode("utf-8")

def generate_rsa_keypair():
    """
    Generates an RSA-2048 keypair.
    Returns (public_key_pem, private_key_pem).
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048
    )
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )

    return public_pem.decode("utf-8"), private_pem.decode("utf-8")
