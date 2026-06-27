from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import unquote

SESSION_COOKIE = "exlibris_session"
SESSION_DAYS = 30
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$", 2)
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return hmac.compare_digest(digest, expected)


def session_secret(*, fallback_seed: str) -> bytes:
    env = os.environ.get("EXLIBRIS_SESSION_SECRET")
    if env:
        return env.encode("utf-8")
    return hashlib.sha256(fallback_seed.encode("utf-8")).digest()


def create_session_token(*, user_id: int, username: str, secret: bytes) -> str:
    expires_at = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{user_id}:{username}:{expires_at}"
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def parse_session_token(token: str, *, secret: bytes) -> tuple[int, str] | None:
    payload, _, signature = token.rpartition(":")
    if not payload or not signature:
        return None
    expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        user_id_s, username, expires_at_s = payload.split(":", 2)
        user_id = int(user_id_s)
        expires_at = int(expires_at_s)
    except ValueError:
        return None
    if expires_at < int(time.time()):
        return None
    return user_id, username


def parse_cookie_header(header: str, name: str) -> str | None:
    for part in header.split(";"):
        part = part.strip()
        if not part.startswith(f"{name}="):
            continue
        return unquote(part.split("=", 1)[1])
    return None


def cookie_path() -> str:
    prefix = os.environ.get("EXLIBRIS_CGI_PREFIX", "/")
    if "cgi-bin" in prefix:
        return prefix.split("cgi-bin", 1)[0] or "/"
    return prefix


def session_cookie_header(token: str) -> str:
    path = cookie_path()
    max_age = SESSION_DAYS * 86400
    return (
        f"Set-Cookie: {SESSION_COOKIE}={token}; Path={path}; "
        f"HttpOnly; SameSite=Lax; Max-Age={max_age}"
    )


def clear_session_cookie_header() -> str:
    path = cookie_path()
    return f"Set-Cookie: {SESSION_COOKIE}=; Path={path}; HttpOnly; SameSite=Lax; Max-Age=0"
