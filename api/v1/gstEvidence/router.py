"""Database-backed API for AWS S3 PDF presigned URLs."""

import os
from functools import lru_cache
import re
from typing import Any

import boto3
from prisma import Json
from helpers.prisma import prisma
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse


S3_REGION = os.getenv("S3_REGION")
BUCKET_NAME = os.getenv("S3_DEFAULT_BUCKET")
S3_KEY_PREFIX = os.getenv("S3_KEY_PREFIX", "").strip("/")
API_ROLE = os.getenv("API_ROLE", "public")

router = APIRouter(tags=["gst-evidence"])


@lru_cache(maxsize=1)
def get_s3_client():
    if not BUCKET_NAME or not S3_REGION:
        raise HTTPException(
            status_code=503,
            detail="S3_DEFAULT_BUCKET and S3_REGION must be configured",
        )
    return boto3.client(
        "s3",
        region_name=S3_REGION,
        endpoint_url=f"https://s3.{S3_REGION}.amazonaws.com",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
    )


async def get_acknowledgment_records(
    company: str,
    state: str,
    district: str,
    month: str,
    financial_year: str,
) -> list[dict[str, object]]:
    """Read matching acknowledgment records and JSON S3 form paths from PostgreSQL."""
    rows = await prisma.query_raw(
        """
                SELECT gstin, form, s3_path
                FROM "Automation"."gstEvidence"
                WHERE LOWER(company) = LOWER($1::text)
                    AND LOWER(state_name) = LOWER($2::text)
                    AND LOWER(district_name) = LOWER($3::text)
                    AND LOWER(month) = LOWER($4::text)
                    AND LOWER(financial_year) = LOWER($5::text)
                ORDER BY month, gstin
                """,
        company, state, district, month, financial_year,
    )
    return [
        {
            "gstin": row["gstin"],
            "forms": row["form"] if isinstance(row["form"], list) else [],
            "s3_paths": row["s3_path"] if isinstance(row["s3_path"], list) else [],
        }
        for row in rows
    ]


def find_pdf_keys(s3_client, s3_prefix: str, form: str | None = None) -> list[str]:
    """List PDF object keys under a key prefix stored in PostgreSQL."""
    stored_prefix = s3_prefix.strip("/")
    if S3_KEY_PREFIX and not (
        stored_prefix == S3_KEY_PREFIX or stored_prefix.startswith(f"{S3_KEY_PREFIX}/")
    ):
        stored_prefix = f"{S3_KEY_PREFIX}/{stored_prefix}" if stored_prefix else S3_KEY_PREFIX
    if stored_prefix and not stored_prefix.endswith("/"):
        stored_prefix += "/"
    paginator = s3_client.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=stored_prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            filename = key.rsplit("/", 1)[-1]
            match = re.match(r"^(GSTR[0-9]+[A-Z]?)_", filename, re.IGNORECASE)
            selected_form = form.replace("-", "").casefold() if form else None
            if key.lower().endswith(".pdf") and (
                not selected_form or (match and match.group(1).casefold() == selected_form)
            ):
                keys.append(key)
    return keys


@router.post("/internal/gst-evidence/batch")
async def upsert_evidence_batch(payload: dict[str, Any]) -> dict[str, int]:
    """Index uploaded evidence PDFs for the internal worker."""
    if API_ROLE != "internal":
        raise HTTPException(status_code=403, detail="GST Evidence writes are internal only")
    records = payload.get("records")
    if not isinstance(records, list) or not records or len(records) > 100:
        raise HTTPException(status_code=422, detail="records must contain between 1 and 100 periods")

    prepared = []
    for item in records:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="Each evidence period must be an object")
        gstin = str(item.get("gstin", "")).strip().upper()
        company = str(item.get("company", "")).strip()
        month = str(item.get("month", "")).strip().casefold()
        financial_year = str(item.get("financial_year", "")).strip()
        year = str(item.get("year", "")).strip()
        month_number = str(item.get("month_number", "")).strip()
        forms = item.get("forms")
        s3_paths = item.get("s3_paths")
        if (not re.fullmatch(r"[0-9A-Z]{15}", gstin) or not company or not month
                or not re.fullmatch(r"20\d{2}", year) or not re.fullmatch(r"0?[1-9]|1[0-2]", month_number)
                or not financial_year or not isinstance(forms, list) or not forms
                or not isinstance(s3_paths, list) or len(forms) != len(s3_paths)):
            raise HTTPException(status_code=422, detail="Invalid GST Evidence period metadata")
        if any(not isinstance(form, str) or not form.strip() for form in forms):
            raise HTTPException(status_code=422, detail="Each form must be a non-empty string")
        if any(not isinstance(path, str) or not path.strip() for path in s3_paths):
            raise HTTPException(status_code=422, detail="Each S3 path must be a non-empty string")
        prepared.append({
            "gstin": gstin,
            "company": company,
            "month": month,
            "financial_year": financial_year,
            "forms": [form.strip().casefold() for form in forms],
            "s3_paths": [path.strip().strip("/") for path in s3_paths],
        })

    written = 0
    async with prisma.tx(timeout=60000) as tx:
        for item in prepared:
            gst_row = await tx.gstfetch.find_unique(where={"gstin": item["gstin"]})
            if gst_row is None:
                raise HTTPException(status_code=404, detail=f"GST Fetch record missing for {item['gstin']}")
            if not gst_row.stateName or not gst_row.districtName:
                raise HTTPException(status_code=409, detail=f"State or district is missing for {item['gstin']}")
            existing = await tx.gstevidence.find_first(where={
                "gstin": item["gstin"],
                "month": item["month"],
                "financialYear": item["financial_year"],
            })
            old_forms = list(existing.form) if existing and isinstance(existing.form, list) else []
            old_paths = list(existing.s3Path) if existing and isinstance(existing.s3Path, list) else []
            by_form = {str(name).casefold(): str(path) for name, path in zip(old_forms, old_paths)}
            by_form.update(dict(zip(item["forms"], item["s3_paths"])))
            merged_forms = list(by_form)
            merged_paths = [by_form[name] for name in merged_forms]
            data = {
                "gstin": item["gstin"],
                "company": item["company"],
                "stateName": gst_row.stateName,
                "districtName": gst_row.districtName,
                "month": item["month"],
                "financialYear": item["financial_year"],
                "form": Json(merged_forms),
                "s3Path": Json(merged_paths),
            }
            if existing:
                await tx.gstevidence.update(where={"id": existing.id}, data=data)
            else:
                await tx.gstevidence.create(data=data)
            written += 1
    return {"periods_written": written}


@router.get("/gstEvidence/presigned-url", response_class=JSONResponse)
async def get_presigned_urls(
    company: str = Query(description="Company code stored in PostgreSQL, for example zetwerk."),
    state: str = Query(description="State name stored in PostgreSQL, for example Andhra Pradesh."),
    district: str = Query(description="District name stored in PostgreSQL, for example Vizianagaram."),
    month: str = Query(description="Month stored in PostgreSQL, for example august."),
    financial_year: str = Query(description="Financial year stored in PostgreSQL, for example 2026-27."),
    form: str = Query(description="Form stored in JSON, for example gstr-1."),
    expires_in: int = Query(default=3600, ge=1, le=604800),
) -> dict[str, object]:
    """Return matching PDF names and AWS S3 presigned URLs."""
    try:
        records = await get_acknowledgment_records(
            company, state, district, month, financial_year,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not query PostgreSQL: {exc}") from exc

    if not records:
        raise HTTPException(
            status_code=404,
            detail=(
                "No acknowledgment record matches the supplied company, state, district, "
                "month, and financial year."
            ),
        )

    prefixes: list[tuple[dict[str, object], str]] = []
    for record in records:
        forms = record["forms"]
        paths = record["s3_paths"]
        if len(forms) != len(paths):
            raise HTTPException(
                status_code=409,
                detail=f"Form and S3 path lists have different lengths for GSTIN {record['gstin']}.",
            )

        try:
            form_index = [str(value).casefold() for value in forms].index(form.casefold())
        except ValueError:
            continue
        prefixes.append((record, str(paths[form_index]).rstrip("/") + "/", str(forms[form_index])))

    if not prefixes:
        raise HTTPException(status_code=404, detail=f"Form '{form}' is not stored for the matching record.")

    files: list[dict[str, str]] = []
    try:
        s3_client = get_s3_client()
        for record, prefix, selected_form in prefixes:
            keys = await run_in_threadpool(find_pdf_keys, s3_client, prefix, selected_form)
            for key in keys:
                url = await run_in_threadpool(
                    s3_client.generate_presigned_url,
                    ClientMethod="get_object",
                    Params={"Bucket": BUCKET_NAME, "Key": key},
                    ExpiresIn=expires_in,
                )
                files.append(
                    {
                        "file_name": key.rsplit("/", 1)[-1],
                        "url": url,
                    }
                )
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(status_code=502, detail=f"Could not access AWS S3: {exc}") from exc

    if not files:
        raise HTTPException(status_code=404, detail="No PDFs found under the matching S3 folder.")

    return {"files": files, "expires_in": expires_in}
