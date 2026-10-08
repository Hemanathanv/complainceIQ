"""Exchange a frontend's private API key for renewable session tokens."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Request, Response

from api.v1.auth.origins import is_same_origin
from api.v1.auth.security import (
    ACCESS_TOKEN_SECONDS,
    COOKIE_NAME,
    REFRESH_TOKEN_SECONDS,
    access_token,
    cookie_secure,
    key_hash,
    new_refresh_token,
)
from helpers.prisma import prisma

router = APIRouter(prefix="/auth", tags=["auth"])


def auth_cookie_path(request: Request) -> str:
    return "/api/v1/auth"


def set_refresh_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=REFRESH_TOKEN_SECONDS,
        path=auth_cookie_path(request),
        httponly=True,
        secure=cookie_secure(),
        samesite="strict",
    )


def check_origin(request: Request, allowed_origins: list[str]) -> None:
    origin = request.headers.get("origin")
    if origin and not is_same_origin(request, origin) and origin not in allowed_origins:
        raise HTTPException(status_code=403, detail="Origin is not allowed for this frontend")


@router.post("/token")
async def create_session(
    request: Request,
    response: Response,
    x_api_key: str | None = Header(default=None),
) -> dict[str, object]:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Frontend API key required")
    client = await prisma.apiclient.find_unique(where={"keyHash": key_hash(x_api_key)})
    if client is None or client.revokedAt is not None:
        raise HTTPException(status_code=401, detail="Invalid frontend API key")
    check_origin(request, client.allowedOrigins)

    now = datetime.now(timezone.utc)
    refresh = new_refresh_token()
    session = await prisma.apisession.create(
        data={
            "clientId": client.id,
            "keyHashAtIssue": client.keyHash,
            "refreshHash": key_hash(refresh),
            "expiresAt": now + timedelta(seconds=REFRESH_TOKEN_SECONDS),
        }
    )
    await prisma.apiclient.update(where={"id": client.id}, data={"lastUsedAt": now})
    set_refresh_cookie(request, response, refresh)
    return {
        "access_token": access_token(client.id, session.id),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_SECONDS,
        "client_id": client.id,
    }


@router.post("/refresh")
async def refresh_session(request: Request, response: Response) -> dict[str, object]:
    refresh = request.cookies.get(COOKIE_NAME)
    if not refresh:
        raise HTTPException(status_code=401, detail="Refresh cookie required")
    old_hash = key_hash(refresh)
    session = await prisma.apisession.find_unique(where={"refreshHash": old_hash})
    now = datetime.now(timezone.utc)
    if session is None or session.revokedAt is not None or session.expiresAt <= now:
        raise HTTPException(status_code=401, detail="Refresh session expired")
    client = await prisma.apiclient.find_unique(where={"id": session.clientId})
    if client is None or client.revokedAt is not None or session.keyHashAtIssue != client.keyHash:
        raise HTTPException(status_code=401, detail="Frontend access revoked")
    check_origin(request, client.allowedOrigins)

    next_refresh = new_refresh_token()
    changed = await prisma.apisession.update_many(
        where={"id": session.id, "refreshHash": old_hash, "revokedAt": None},
        data={"refreshHash": key_hash(next_refresh)},
    )
    if changed != 1:
        raise HTTPException(status_code=401, detail="Refresh token was already used")
    await prisma.apiclient.update(where={"id": client.id}, data={"lastUsedAt": now})
    set_refresh_cookie(request, response, next_refresh)
    return {
        "access_token": access_token(client.id, session.id),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_SECONDS,
        "client_id": client.id,
    }


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    refresh = request.cookies.get(COOKIE_NAME)
    if refresh:
        session = await prisma.apisession.find_unique(where={"refreshHash": key_hash(refresh)})
        if session is not None:
            client = await prisma.apiclient.find_unique(where={"id": session.clientId})
            if client is not None:
                check_origin(request, client.allowedOrigins)
            await prisma.apisession.update(
                where={"id": session.id},
                data={"revokedAt": datetime.now(timezone.utc)},
            )
    response.delete_cookie(COOKIE_NAME, path=auth_cookie_path(request))
    return {"status": "logged_out"}
