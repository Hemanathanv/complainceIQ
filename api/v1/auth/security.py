"""Server-side token primitives. The signing key never goes to a browser."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
import secrets

import jwt
from jwt import InvalidTokenError

ACCESS_TOKEN_SECONDS = 15 * 60
REFRESH_TOKEN_SECONDS = 7 * 24 * 60 * 60
COOKIE_NAME = "ciq_refresh"
ISSUER = "complainceiq-api"
AUDIENCE = "complainceiq-frontend"


def signing_key() -> str:
    key = os.getenv("API_JWT_SIGNING_KEY", "")
    if len(key) < 32:
        raise RuntimeError("API_JWT_SIGNING_KEY must contain at least 32 characters")
    return key


def key_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def new_client_key() -> str:
    return "ciq_" + secrets.token_urlsafe(32)


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def cookie_secure() -> bool:
    return os.getenv("API_COOKIE_SECURE", "true").lower() == "true"


def access_token(client_id: str, session_id: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": client_id,
            "sid": session_id,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + timedelta(seconds=ACCESS_TOKEN_SECONDS),
        },
        signing_key(),
        algorithm="HS256",
    )


def decode_access_token(token: str) -> dict[str, str]:
    try:
        claims = jwt.decode(
            token,
            signing_key(),
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["sub", "sid", "iat", "exp", "iss", "aud"]},
        )
        if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("sid"), str):
            raise InvalidTokenError("Invalid subject or session")
        return claims
    except InvalidTokenError as exc:
        raise ValueError("Invalid or expired access token") from exc
