"""Database-backed API for SeaweedFS PDF presigned URLs."""

import os
from pathlib import Path
from uuid import UUID

import boto3
import psycopg
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool


load_dotenv(Path(__file__).with_name(".env"))

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://192.168.10.100:8333")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_ACCESS_KEY = os.getenv("S3_ADMIN_ACCESS")
S3_SECRET_KEY = os.getenv("S3_ADMIN_SECRET")
BUCKET_NAME = "compliance"

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

if not S3_ACCESS_KEY or not S3_SECRET_KEY:
    raise RuntimeError("S3_ADMIN_ACCESS and S3_ADMIN_SECRET must be configured")
if not all((DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD)):
    raise RuntimeError("DB_HOST, DB_PORT, DB_NAME, DB_USER, and DB_PASSWORD must be configured")

s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    region_name=S3_REGION,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)

app = FastAPI(
    title="GST Acknowledgment Document API",
    description="Find PDF files from PostgreSQL metadata and return SeaweedFS presigned URLs.",
    version="3.0.0",
)


class PresignedFile(BaseModel):
    source_uuid: UUID
    gstin: str
    month: str | None
    file_name: str
    key: str
    url: str


class PresignedUrlResponse(BaseModel):
    company: str
    state: str
    district: str
    month: str
    financial_year: str
    form: str
    files: list[PresignedFile]
    expires_in: int


def get_records(
    company: str,
    state: str,
    district: str,
    month: str,
    financial_year: str,
) -> list[dict[str, object]]:
    """Read matching acknowledgment records and JSON S3 form paths from PostgreSQL."""
    with psycopg.connect(
        host=DB_HOST,
        port=int(DB_PORT),
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=5,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_uuid, gstin, month, form, s3_path
                FROM gstacknowledge.gstin_profiles
                WHERE LOWER(company) = LOWER(%s)
                    AND LOWER(state_name) = LOWER(%s)
                    AND LOWER(district_name) = LOWER(%s)
                    AND LOWER(month) = LOWER(%s)
                    AND LOWER(financial_year) = LOWER(%s)
                ORDER BY month, gstin
                """,
                (company, state, district, month, financial_year),
            )
            rows = cursor.fetchall()

    records = []
    for source_uuid, gstin, month, forms, paths in rows:
        records.append(
            {
                "source_uuid": source_uuid,
                "gstin": gstin,
                "month": month,
                "forms": forms if isinstance(forms, list) else [],
                "s3_paths": paths if isinstance(paths, list) else [],
            }
        )
    return records


def find_pdf_keys(s3_prefix: str) -> list[str]:
    """List PDF object keys under one S3 prefix stored in PostgreSQL."""
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=s3_prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.lower().endswith(".pdf"):
                keys.append(key)
    return keys


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "source": "PostgreSQL + SeaweedFS"}


@app.get("/acknowledgments/presigned-url", response_model=PresignedUrlResponse)
async def get_presigned_urls(
    company: str = Query(description="Company code stored in PostgreSQL, for example zetwerk."),
    state: str = Query(description="State name stored in PostgreSQL, for example Andhra Pradesh."),
    district: str = Query(description="District name stored in PostgreSQL, for example Vizianagaram."),
    month: str = Query(description="Month stored in PostgreSQL, for example august."),
    financial_year: str = Query(description="Financial year stored in PostgreSQL, for example 2026-27."),
    form: str = Query(description="Form stored in JSON, for example gstr-1."),
    expires_in: int = Query(default=3600, ge=1, le=604800),
) -> PresignedUrlResponse:
    """Find PDFs by company, state, district, and form, then create presigned URLs."""
    try:
        records = await run_in_threadpool(
            get_records,
            company,
            state,
            district,
            month,
            financial_year,
        )
    except (ValueError, psycopg.Error) as exc:
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
        prefixes.append((record, str(paths[form_index]).rstrip("/") + "/"))

    if not prefixes:
        raise HTTPException(status_code=404, detail=f"Form '{form}' is not stored for the matching record.")

    files: list[PresignedFile] = []
    try:
        for record, prefix in prefixes:
            keys = await run_in_threadpool(find_pdf_keys, prefix)
            for key in keys:
                url = await run_in_threadpool(
                    s3.generate_presigned_url,
                    ClientMethod="get_object",
                    Params={"Bucket": BUCKET_NAME, "Key": key},
                    ExpiresIn=expires_in,
                )
                files.append(
                    PresignedFile(
                        source_uuid=record["source_uuid"],
                        gstin=str(record["gstin"]),
                        month=record["month"],
                        file_name=key.rsplit("/", 1)[-1],
                        key=key,
                        url=url,
                    )
                )
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(status_code=502, detail=f"Could not access SeaweedFS: {exc}") from exc

    if not files:
        raise HTTPException(status_code=404, detail="No PDFs found under the matching S3 folder.")

    return PresignedUrlResponse(
        company=company,
        state=state,
        district=district,
        month=month,
        financial_year=financial_year,
        form=form,
        files=files,
        expires_in=expires_in,
    )
