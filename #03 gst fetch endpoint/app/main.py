import os
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Any
from uuid import UUID

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path
from psycopg.rows import dict_row


load_dotenv(FilePath(__file__).resolve().parents[1] / ".env")

TABLE = "gstfetch.gstin_cache"
ALLOWED_COLUMNS = {
    "id",
    "gstin",
    "business_info",
    "filing_tables",
    "legal_name",
    "status",
    "fetched_at",
    "fetch_count",
    "created_at",
}


def db_connection() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=10,
        row_factory=dict_row,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing environment variable(s): {', '.join(missing)}")
    yield


app = FastAPI(
    title="GST Fetch API",
    version="1.0.0",
    description="Versioned API for GST cache records.",
    lifespan=lifespan,
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/gst-cache/{record_uuid}", tags=["gst-cache"])
def get_row(record_uuid: UUID = Path(description="UUID from gstfetch.gstin_cache.id")) -> dict[str, Any]:
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {TABLE} WHERE id = %s", (record_uuid,))
            row = cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="GST cache row not found")
    return dict(row)


@app.get("/api/v1/gst-cache/{record_uuid}/{column_name}", tags=["gst-cache"])
def get_column(
    record_uuid: UUID = Path(description="UUID from gstfetch.gstin_cache.id"),
    column_name: str = Path(description="Allowed column name"),
) -> dict[str, Any]:
    if column_name not in ALLOWED_COLUMNS:
        raise HTTPException(status_code=400, detail="Invalid or unsupported column name")

    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {column_name} FROM {TABLE} WHERE id = %s",
                (record_uuid,),
            )
            row = cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="GST cache row not found")
    return {"uuid": str(record_uuid), "column": column_name, "value": row[column_name]}
