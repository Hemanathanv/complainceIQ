"""Read-only FastAPI endpoints for the compliance S3 bucket."""

import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from starlette.responses import StreamingResponse


load_dotenv(Path(__file__).with_name(".env"))

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://192.168.10.100:8333")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_ACCESS_KEY = os.getenv("S3_ADMIN_ACCESS")
S3_SECRET_KEY = os.getenv("S3_ADMIN_SECRET")
BUCKET_NAME = "compliance"

if not S3_ACCESS_KEY or not S3_SECRET_KEY:
    raise RuntimeError(
        "S3_ADMIN_ACCESS and S3_ADMIN_SECRET must be set in .env or the process environment"
    )

s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    region_name=S3_REGION,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    config=Config(connect_timeout=5, read_timeout=30),
)

app = FastAPI(
    title="Compliance S3 Reader",
    description="Read-only API for listing and downloading objects from the compliance bucket.",
    version="1.0.0",
)


class S3Object(BaseModel):
    key: str
    size: int
    last_modified: datetime
    etag: str | None = None


class ObjectList(BaseModel):
    bucket: str
    prefix: str
    objects: list[S3Object]
    next_continuation_token: str | None = None


def raise_s3_http_error(exc: ClientError, *, missing_is_404: bool = False) -> None:
    error = exc.response.get("Error", {})
    code = error.get("Code", "S3Error")
    message = error.get("Message", "S3 request failed")

    if code in ("AccessDenied", "Forbidden", "403"):
        status_code = 403
    elif missing_is_404 and code in ("NoSuchKey", "NoSuchBucket", "NotFound", "404"):
        status_code = 404
    else:
        status_code = 502

    raise HTTPException(status_code=status_code, detail=f"S3 {code}: {message}") from exc


def document_prefix(company: str, state: str, month: str, form_name: str) -> str:
    """Build the S3 prefix: company/state/month/form/."""
    parts = (company, state, month, form_name)
    if any(not part.strip() or "/" in part or "\\" in part for part in parts):
        raise HTTPException(
            status_code=422,
            detail="Company, state, month, and form must each be one folder name.",
        )
    return "/".join(part.strip() for part in parts) + "/"


def stream_s3_object(key: str) -> StreamingResponse:
    try:
        response = s3.get_object(Bucket=BUCKET_NAME, Key=key)
    except ClientError as exc:
        raise_s3_http_error(exc, missing_is_404=True)
    except BotoCoreError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach S3: {exc}") from exc

    body = response["Body"]
    filename = quote(key.rsplit("/", 1)[-1], safe="")
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    if "ContentLength" in response:
        headers["Content-Length"] = str(response["ContentLength"])

    def stream_object():
        try:
            yield from body.iter_chunks(chunk_size=1024 * 1024)
        finally:
            body.close()

    return StreamingResponse(
        stream_object(),
        media_type=response.get("ContentType", "application/octet-stream"),
        headers=headers,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "bucket": BUCKET_NAME}


@app.get("/objects", response_model=ObjectList)
def list_objects(
    prefix: str = "",
    max_keys: int = Query(default=100, ge=1, le=1000),
    continuation_token: str | None = None,
) -> ObjectList:
    """List one page of objects. Pass the returned token to get the next page."""
    params: dict[str, object] = {
        "Bucket": BUCKET_NAME,
        "Prefix": prefix,
        "MaxKeys": max_keys,
    }
    if continuation_token:
        params["ContinuationToken"] = continuation_token

    try:
        response = s3.list_objects_v2(**params)
    except ClientError as exc:
        raise_s3_http_error(exc)
    except BotoCoreError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach S3: {exc}") from exc

    objects = [
        S3Object(
            key=item["Key"],
            size=item["Size"],
            last_modified=item["LastModified"],
            etag=item.get("ETag"),
        )
        for item in response.get("Contents", [])
    ]
    return ObjectList(
        bucket=BUCKET_NAME,
        prefix=prefix,
        objects=objects,
        next_continuation_token=response.get("NextContinuationToken"),
    )


@app.get("/documents/{company}/{state}/{month}/{form_name}", response_model=ObjectList)
def list_document_files(
    company: str,
    state: str,
    month: str,
    form_name: str,
    max_keys: int = Query(default=100, ge=1, le=1000),
    continuation_token: str | None = None,
) -> ObjectList:
    """List files under company/state/month/form/."""
    prefix = document_prefix(company, state, month, form_name)
    return list_objects(
        prefix=prefix,
        max_keys=max_keys,
        continuation_token=continuation_token,
    )


@app.get("/documents/{company}/{state}/{month}/{form_name}/files/{file_path:path}")
def download_document_file(
    company: str,
    state: str,
    month: str,
    form_name: str,
    file_path: str,
) -> StreamingResponse:
    """Download a file below company/state/month/form/."""
    prefix = document_prefix(company, state, month, form_name)
    if not file_path or file_path.startswith("/") or "\\" in file_path:
        raise HTTPException(status_code=422, detail="file_path must be a valid S3 object path.")
    return stream_s3_object(prefix + file_path)


@app.get("/objects/{key:path}")
def download_object(key: str) -> StreamingResponse:
    """Download an object by its full S3 key, such as zetwerk/docs/file.pdf."""
    return stream_s3_object(key)
