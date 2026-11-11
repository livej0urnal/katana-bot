import base64
import hashlib
import hmac
import json
import secrets
import time


def sign(secret: str, purpose: str, user_id: int, chat_id: int, ttl: int) -> str:
    body = json.dumps([purpose, user_id, chat_id, int(time.time()) + ttl, secrets.token_hex(12)])
    data = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    digest = hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}.{digest}"


def verify(secret: str, purpose: str, token: str) -> tuple[int, int, str] | None:
    if not secret or len(token) > 512:
        return None
    try:
        data, digest = token.split(".")
        expected = hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, digest):
            return None
        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        token_purpose, user_id, chat_id, deadline, nonce = json.loads(decoded)
        if token_purpose != purpose or deadline <= time.time():
            return None
        return user_id, chat_id, nonce
    except (ValueError, TypeError):
        return None
