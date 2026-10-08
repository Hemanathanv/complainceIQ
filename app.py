"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPBearer

load_dotenv(Path(__file__).resolve().parent / ".env")

from helpers.prisma import connect_prisma, disconnect_prisma
from api.v1.gstEvidence import router as gst_evidence_router
from api.v1.gstFetch import router as gst_fetch_router
from api.v1.auth.router import router as auth_router
from api.v1.auth.origins import is_same_origin
from api.v1.auth.security import decode_access_token, signing_key
from helpers.prisma import prisma

API_ROLE = os.getenv("API_ROLE", "public")
if API_ROLE not in {"public", "internal"}:
    raise RuntimeError("API_ROLE must be public or internal")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if API_ROLE == "public":
        signing_key()
    await connect_prisma()
    try:
        yield
    finally:
        await disconnect_prisma()


app = FastAPI(
    title="ComplianceIQ API",
    version="1.0.0",
    description="Evidence APIs for external applications.",
    lifespan=lifespan,
)
if API_ROLE == "public":
    bearer_in_docs = HTTPBearer(auto_error=False, description="Access token from POST /api/v1/auth/token")
    app.include_router(gst_fetch_router, prefix="/api/v1", dependencies=[Depends(bearer_in_docs)])
    app.include_router(gst_evidence_router, prefix="/api/v1", dependencies=[Depends(bearer_in_docs)])
    app.include_router(auth_router, prefix="/api/v1")
else:
    from api.v1.gstFetch.sync import router as sync_router
    app.include_router(gst_fetch_router, prefix="/api/v1")
    app.include_router(gst_evidence_router, prefix="/api/v1")
    app.include_router(sync_router, prefix="/api/v1")


PUBLIC_PATHS = {"/api/v1/health", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


@app.middleware("http")
async def authenticate(request: Request, call_next):
    if API_ROLE == "internal":
        return await call_next(request)

    path = request.url.path
    origin = request.headers.get("origin")
    allowed_origin = True
    if origin:
        allowed_origin = is_same_origin(request, origin)
        if not allowed_origin:
            clients = await prisma.apiclient.find_many(where={"revokedAt": None})
            allowed_origin = any(origin in client.allowedOrigins for client in clients)
    if request.method == "OPTIONS":
        response = Response(status_code=204 if allowed_origin else 403)
    elif origin and not allowed_origin:
        response = JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
    elif path in PUBLIC_PATHS or path.startswith("/api/v1/auth/"):
        response = await call_next(request)
    else:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            response = JSONResponse({"detail": "Bearer token required"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        else:
            try:
                claims = decode_access_token(authorization[7:])
                client = await prisma.apiclient.find_unique(where={"id": claims["sub"]})
                session = await prisma.apisession.find_unique(where={"id": claims["sid"]})
                valid = (client is not None and client.revokedAt is None and
                         session is not None and session.clientId == client.id and
                         session.revokedAt is None and session.keyHashAtIssue == client.keyHash and
                         session.expiresAt > datetime.now(timezone.utc) and
                         (not origin or is_same_origin(request, origin) or origin in client.allowedOrigins))
            except (ValueError, RuntimeError):
                valid = False
            if not valid:
                response = JSONResponse({"detail": "Invalid or revoked bearer token"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            else:
                request.state.api_client_id = client.id
                response = await call_next(request)
                try:
                    await prisma.apirequestaudit.create(data={
                        "clientId": client.id, "sessionId": session.id,
                        "method": request.method, "path": path, "statusCode": response.status_code,
                    })
                    await prisma.apiclient.update(where={"id": client.id}, data={"lastUsedAt": datetime.now(timezone.utc)})
                except Exception:
                    logging.getLogger(__name__).exception("Could not record API request audit")
    if allowed_origin and origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-API-Key"
        response.headers["Vary"] = "Origin"
    return response


@app.get("/api/v1/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}
