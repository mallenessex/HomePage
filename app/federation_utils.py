import httpx
import base64
import datetime
import json
import urllib.parse
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography import exceptions
from .config import settings
from . import models
import logging

logger = logging.getLogger(__name__)

async def verify_http_signature(request):
    """
    Verifies the HTTP Signature on an incoming request.
    Follows ActivityPub/Mastodon conventions.
    """
    signature_header = request.headers.get("Signature")
    if not signature_header:
        return False

    # 1. Parse Signature Header
    # Format: keyId="...", algorithm="...", headers="...", signature="..."
    parts = {}
    for part in signature_header.split(','):
        if '=' in part:
            k, v = part.split('=', 1)
            parts[k.strip()] = v.strip('"')

    key_id = parts.get("keyId")
    algorithm = parts.get("algorithm")
    headers_list = parts.get("headers", "date").split()
    signature_b64 = parts.get("signature")

    if not key_id or not signature_b64:
        return False

    # 2. Fetch Remote Public Key
    # In production, cache these!
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(key_id, headers={"Accept": "application/activity+json"})
            if resp.status_code != 200:
                # Try fetching without LD context if it fails?
                return False
            actor_data = resp.json()
            
            # Key might be embedded or linked
            public_key_data = actor_data
            if "publicKey" in actor_data:
                public_key_data = actor_data["publicKey"]
            
            public_key_pem = public_key_data.get("publicKeyPem")
            if not public_key_pem:
                return False
                
            public_key = serialization.load_pem_public_key(public_key_pem.encode())
    except Exception as e:
        print(f"Error fetching remote key: {e}")
        return False

    # 3. Construct String to Sign
    # (request-target) is special: "post /users/username/inbox"
    to_sign_parts = []
    for h in headers_list:
        if h == "(request-target)":
            val = f"{request.method.lower()} {request.url.path}"
        else:
            val = request.headers.get(h)
            if not val:
                return False
        to_sign_parts.append(f"{h}: {val}")
    
    to_sign = "\n".join(to_sign_parts).encode()

    # 4. Verify
    try:
        signature = base64.b64decode(signature_b64)
        # AP standard is usually rsa-sha256 or hmac-sha256 (not used for actors)
        # We assume RSA-SHA256 for now.
        public_key.verify(
            signature,
            to_sign,
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        return True
    except exceptions.InvalidSignature:
        return False
    except Exception as e:
        print(f"Verification error: {e}")
        return False

async def sign_and_send_activity(user: models.User, activity: dict, recipient_inbox: str):
    """
    Signs an activity with the user's RSA private key and POSTs it to a remote inbox.
    """
    body = json.dumps(activity).encode()
    date_str = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    
    parsed_url = urllib.parse.urlparse(recipient_inbox)
    path = parsed_url.path
    if parsed_url.query:
        path += f"?{parsed_url.query}"
    
    headers = {
        "host": parsed_url.netloc,
        "date": date_str,
        "content-type": "application/activity+json"
    }
    
    # 1. Sign
    # headers to sign: (request-target), host, date
    headers_to_sign = ["(request-target)", "host", "date"]
    to_sign_parts = []
    for h in headers_to_sign:
        if h == "(request-target)":
            to_sign_parts.append(f"(request-target): post {path}")
        else:
            to_sign_parts.append(f"{h}: {headers[h]}")
    
    to_sign = "\n".join(to_sign_parts).encode()
    
    # Load private key
    # In production, decrypt this first!
    private_key = serialization.load_pem_private_key(
        user.rsa_private_key_enc.encode(),
        password=None
    )
    
    signature = private_key.sign(
        to_sign,
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    
    sig_b64 = base64.b64encode(signature).decode()
    key_id = f"{settings.BASE_URL}/users/{user.username}#main-key"
    
    sig_header = (
        f'keyId="{key_id}",'
        f'algorithm="rsa-sha256",'
        f'headers="{" ".join(headers_to_sign)}",'
        f'signature="{sig_b64}"'
    )
    headers["Signature"] = sig_header
    
    # 2. Send
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(recipient_inbox, content=body, headers=headers)
            logger.info(f"Delivery to {recipient_inbox}: {resp.status_code}")
            return resp.status_code
    except Exception as e:
        logger.error(f"Delivery failed to {recipient_inbox}: {e}")
        return None

async def perform_webfinger(account_handle: str):
    """
    Performs WebFinger lookup for a handle like 'user@domain.com'.
    Returns the ActivityPub actor URL if found.
    """
    if "@" not in account_handle:
        return None
        
    username, domain = account_handle.split("@", 1)
    
    # Try HTTPS first, then fallback to HTTP for local dev? 
    # Actually ActivityPub *requires* HTTPS usually, but for local dev we might need to be flexible.
    # We'll try the protocol defined in settings as a hint, or just try https.
    
    # Try current protocol first, then the other
    primary = settings.PROTOCOL
    secondary = "https" if primary == "http" else "http"
    
    protocols = [primary, secondary]
    for proto in protocols:
        url = f"{proto}://{domain}/.well-known/webfinger?resource=acct:{account_handle}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=2.0)
                if resp.status_code == 200:
                    data = resp.json()
                    for link in data.get("links", []):
                        if link.get("rel") == "self" and link.get("type") == "application/activity+json":
                            return link.get("href")
        except Exception:
            continue
    return None

async def discover_remote_actor(actor_url: str):
    """
    Fetches the Actor JSON-LD document from the given URL.
    Returns the parsed dict.
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(actor_url, headers={"Accept": "application/activity+json"}, timeout=5.0)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"Failed to fetch actor {actor_url}: {resp.status_code}")
    except Exception as e:
        logger.error(f"Error discovering actor {actor_url}: {e}")
    return None


