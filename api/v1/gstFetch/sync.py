"""Synchronize all Infisical project GSTIN secrets into the GST cache."""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os

from fastapi import APIRouter, HTTPException
import requests

from helpers.prisma import prisma

router = APIRouter(tags=["internal"])
logger = logging.getLogger(__name__)


def _infisical_secrets() -> tuple[str, str, list[dict]]:
    base_url = os.getenv("INFISICAL_API_URL", "http://infisical:8080").rstrip("/")
    project = os.getenv("INFISICAL_PROJECT_ID", "")
    environment = os.getenv("INFISICAL_ENVIRONMENT", "dev")
    if not project:
        raise RuntimeError("INFISICAL_PROJECT_ID is required")
    token = os.getenv("INFISICAL_TOKEN", "")
    if not token:
        client_id = os.getenv("INFISICAL_CLIENT_ID", "")
        client_secret = os.getenv("INFISICAL_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            raise RuntimeError("Configure an Infisical token or machine identity credentials")
        login = requests.post(
            f"{base_url}/api/v1/auth/universal-auth/login",
            json={"clientId": client_id, "clientSecret": client_secret}, timeout=20,
        )
        login.raise_for_status()
        token = login.json()["accessToken"]
    response = requests.get(
        f"{base_url}/api/v3/secrets/raw",
        params={"workspaceId": project, "environment": environment,
                "secretPath": "/", "recursive": "true", "include_imports": "false",
                "viewSecretValue": "true"},
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    response.raise_for_status()
    secrets = response.json().get("secrets")
    if not isinstance(secrets, list):
        raise RuntimeError("Infisical returned no secrets array")
    return project, environment, secrets


@router.post("/internal/gst-cache/sync")
async def sync_gst_cache() -> dict[str, int]:
    try:
        project, environment, secrets = await asyncio.to_thread(_infisical_secrets)
    except RuntimeError as exc:
        logger.warning("Infisical sync configuration error: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        logger.warning("Infisical sync request failed with HTTP %s", status)
        raise HTTPException(status_code=503, detail=f"Infisical request failed with HTTP {status}") from exc
    except requests.RequestException as exc:
        logger.warning("Infisical sync connection failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail=f"Could not reach Infisical ({type(exc).__name__})") from exc
    except (KeyError, ValueError) as exc:
        logger.warning("Infisical returned an invalid authentication or secrets response")
        raise HTTPException(status_code=503, detail="Infisical returned an invalid response") from exc

    scope = f"{project}:{environment}:"
    incoming: dict[str, tuple[str, str]] = {}
    gstins: set[str] = set()
    for item in secrets:
        name = item.get("secretKey")
        value = item.get("secretValue")
        if not isinstance(name, str) or not isinstance(value, str):
            raise HTTPException(status_code=502, detail="Infisical secret lacks a name or value")
        secret_value = value.strip()
        if secret_value.startswith("{"):
            try:
                credential = json.loads(secret_value)
            except json.JSONDecodeError as exc:
                logger.warning("Infisical credential secret %s contains invalid JSON", name)
                raise HTTPException(status_code=422, detail=f"Infisical credential secret {name!r} is invalid JSON") from exc
            if not isinstance(credential, dict):
                raise HTTPException(status_code=422, detail=f"Infisical credential secret {name!r} must be a JSON object")
            secret_value = credential.get("gst_no", "")
            if not isinstance(secret_value, str):
                raise HTTPException(status_code=422, detail=f"Infisical credential secret {name!r} has no valid gst_no")
        gstin = secret_value.strip().upper()
        if len(gstin) != 15 or not gstin.isalnum():
            raise HTTPException(status_code=422, detail=f"Infisical secret {name!r} is not a GSTIN")
        identity = item.get("id") or f"{item.get('secretPath', '/')}:{name}"
        source_key = scope + str(identity)
        if source_key in incoming or gstin in gstins:
            raise HTTPException(status_code=409, detail="Duplicate Infisical secret identity or GSTIN")
        incoming[source_key] = (name, gstin)
        gstins.add(gstin)

    now = datetime.now(timezone.utc)
    try:
        async with prisma.tx(timeout=60000) as tx:
            rows = await tx.gstfetch.find_many()
            by_source = {row.sourceKey: row for row in rows if row.sourceKey}
            by_gstin = {row.gstin: row for row in rows}
            for source_key, (name, gstin) in incoming.items():
                source_row = by_source.get(source_key)
                gstin_row = by_gstin.get(gstin)
                if source_row and gstin_row and source_row.id != gstin_row.id:
                    raise HTTPException(status_code=409, detail="GSTIN is assigned to another secret")
                if gstin_row and gstin_row.sourceKey and gstin_row.sourceKey != source_key:
                    # Infisical secret IDs can change when secrets are moved
                    # between folders. Preserve the cache row by GSTIN and
                    # rebind its identity only within this project/environment.
                    if not gstin_row.sourceKey.startswith(scope):
                        raise HTTPException(status_code=409, detail="GSTIN is assigned to another secret")
                row = source_row or gstin_row
                data = {"gstin": gstin, "sourceKey": source_key, "sourceName": name,
                        "isActive": True, "syncedAt": now}
                if row:
                    await tx.gstfetch.update(where={"id": row.id}, data=data)
                else:
                    await tx.gstfetch.create(data=data)
            deactivated = 0
            for row in rows:
                if (row.sourceKey and row.sourceKey.startswith(scope)
                        and row.sourceKey not in incoming and row.gstin not in gstins and row.isActive):
                    await tx.gstfetch.update(where={"id": row.id}, data={"isActive": False, "syncedAt": now})
                    deactivated += 1
    except HTTPException:
        raise
    return {"active": len(incoming), "deactivated": deactivated}
