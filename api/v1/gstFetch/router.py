from datetime import datetime, timezone
import logging
import os
import re
from typing import Any
from uuid import UUID

from prisma import Json

from helpers.prisma import prisma
from fastapi import APIRouter, HTTPException, Path

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
    "state_name",
    "district_name",
}


router = APIRouter(tags=["gst-cache"])
logger = logging.getLogger(__name__)


def prepare_gst_result(record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Validate one scraper result and map it to the GST cache columns."""
    try:
        record_uuid = str(UUID(str(record.get("id", ""))))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="Each result needs a valid record id") from exc
    gst_data = record.get("gst_data")
    if not isinstance(gst_data, dict):
        raise HTTPException(status_code=422, detail="Each result needs a gst_data object")

    business_info = gst_data.get("business_info")
    filing_tables = gst_data.get("filing_tables") or {}
    if not isinstance(business_info, dict) or not business_info:
        raise HTTPException(status_code=422, detail="business_info is required")
    if not isinstance(filing_tables, dict):
        raise HTTPException(status_code=422, detail="filing_tables must be an object")

    gstin = str(gst_data.get("gst_number", "")).strip().upper()
    if len(gstin) != 15 or not gstin.isalnum():
        raise HTTPException(status_code=422, detail="gst_number must be a 15-character GSTIN")

    business_info = dict(business_info)
    filing_frequency = business_info.pop("Return Filing Frequency", None)
    business_info = {"GSTIN": gstin, **business_info}
    filing_tables = {"Return Filing Frequency": filing_frequency, **filing_tables}

    legal_name = business_info.get("Legal Name of Business") or gst_data.get("Trade Name")
    status = business_info.get("GSTIN / UIN  Status") or gst_data.get("GSTIN / UIN  Status")
    if not legal_name:
        raise HTTPException(status_code=422, detail="Legal Name of Business is required")
    if not status:
        raise HTTPException(status_code=422, detail="GSTIN / UIN Status is required")

    address = str(business_info.get("Principal Place of Business") or "")
    location_match = re.search(r",\s*([^,]+),\s*([^,]+),\s*\d{6}\s*$", address)
    district_name = location_match.group(1).strip() if location_match else None
    state_name = location_match.group(2).strip() if location_match else None
    return record_uuid, {
        "gstin": gstin,
        "businessInfo": business_info,
        "filingTables": filing_tables,
        "legalName": legal_name,
        "status": status,
        "stateName": state_name,
        "districtName": district_name,
        "fetchedAt": datetime.now(timezone.utc),
        "fetchCount": {"increment": 1},
    }


def serialize_record(record: Any) -> dict[str, Any]:
    data = record.model_dump(mode="json")
    return {
        "id": data["id"], "gstin": data["gstin"],
        "business_info": data.get("businessInfo"),
        "filing_tables": data.get("filingTables"),
        "legal_name": data.get("legalName"), "status": data.get("status"),
        "fetched_at": data.get("fetchedAt"), "fetch_count": data.get("fetchCount"),
        "created_at": data.get("createdAt"), "state_name": data.get("stateName"),
        "district_name": data.get("districtName"),
    }


@router.get("/gst-cache/records")
async def get_records() -> list[dict[str, str]]:
    """Return synced active records for Temporal workflows to process."""
    rows = await prisma.query_raw(
        'SELECT "id", "gstin" FROM "Automation"."gstFetch" '
        'WHERE "is_active" = true AND "source_key" IS NOT NULL ORDER BY "id" ASC'
    )
    return [{"id": str(row["id"]), "gstin": str(row["gstin"])} for row in rows]


@router.put("/gst-cache/{record_uuid}")
async def update_record(
    record_uuid: UUID,
    gst_data: dict[str, Any],
) -> dict[str, str]:
    """Persist one GST bot result to its existing cache row."""
    if os.getenv("API_ROLE") != "internal":
        raise HTTPException(status_code=403, detail="GST cache writes are internal only")
    business_info = gst_data.get("business_info")
    filing_tables = gst_data.get("filing_tables") or {}
    if not isinstance(business_info, dict) or not business_info:
        raise HTTPException(status_code=422, detail="business_info is required")
    if not isinstance(filing_tables, dict):
        raise HTTPException(status_code=422, detail="filing_tables must be an object")

    gstin = str(gst_data.get("gst_number", "")).strip().upper()
    if len(gstin) != 15:
        raise HTTPException(status_code=422, detail="gst_number must be a 15-character GSTIN")

    business_info = dict(business_info)
    filing_frequency = business_info.pop("Return Filing Frequency", None)
    business_info = {"GSTIN": gstin, **business_info}
    filing_tables = {"Return Filing Frequency": filing_frequency, **filing_tables}

    legal_name = business_info.get("Legal Name of Business") or gst_data.get("Trade Name")
    status = business_info.get("GSTIN / UIN  Status") or gst_data.get("GSTIN / UIN  Status")
    if not legal_name:
        raise HTTPException(status_code=422, detail="Legal Name of Business is required")
    if not status:
        raise HTTPException(status_code=422, detail="GSTIN / UIN Status is required")

    address = str(business_info.get("Principal Place of Business") or "")
    location_match = re.search(r",\s*([^,]+),\s*([^,]+),\s*\d{6}\s*$", address)
    district_name = location_match.group(1).strip() if location_match else None
    state_name = location_match.group(2).strip() if location_match else None

    existing = await prisma.gstfetch.find_unique(where={"id": str(record_uuid)})
    if existing is None:
        raise HTTPException(status_code=404, detail="GST cache row not found")
    if not existing.isActive or existing.gstin != gstin:
        raise HTTPException(status_code=409, detail="GSTIN no longer matches an active cache row")
    await prisma.gstfetch.update(
        where={"id": str(record_uuid)},
        data={
            "businessInfo": Json(business_info),
            "filingTables": Json(filing_tables),
            "legalName": legal_name,
            "status": status,
            "stateName": state_name,
            "districtName": district_name,
            "fetchedAt": datetime.now(timezone.utc),
            "fetchCount": {"increment": 1},
        },
    )

    return {"id": str(record_uuid), "status": "updated"}


@router.post("/internal/gst-cache/batch")
async def update_records_batch(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a batch of scraper results in one database transaction."""
    if os.getenv("API_ROLE") != "internal":
        raise HTTPException(status_code=403, detail="GST cache writes are internal only")
    records = payload.get("records")
    if not isinstance(records, list) or not records or len(records) > 4:
        raise HTTPException(status_code=422, detail="records must contain between 1 and 4 results")

    prepared = []
    rejected = []
    for record in records:
        if not isinstance(record, dict):
            rejected.append({"id": "", "error": "Each result must be an object"})
            continue
        try:
            prepared.append(prepare_gst_result(record))
        except HTTPException as exc:
            rejected.append({"id": str(record.get("id", "")), "error": str(exc.detail)})
    if not prepared:
        return {"updated": 0, "ids": [], "failed_records": rejected}
    ids = [record_id for record_id, _ in prepared]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="Duplicate record id in batch")

    try:
        async with prisma.tx(timeout=60000) as tx:
            for record_id, data in prepared:
                existing = await tx.gstfetch.find_unique(where={"id": record_id})
                if existing is None:
                    raise HTTPException(status_code=404, detail="GST cache row not found")
                if not existing.isActive or existing.gstin != data["gstin"]:
                    raise HTTPException(status_code=409, detail="GSTIN no longer matches an active cache row")
                await tx.gstfetch.update(
                    where={"id": record_id},
                    data={
                        **{key: value for key, value in data.items() if key not in {"gstin", "businessInfo", "filingTables"}},
                        "businessInfo": Json(data["businessInfo"]),
                        "filingTables": Json(data["filingTables"]),
                    },
                )
    except HTTPException:
        raise
    except Exception:
        logger.exception("GST cache batch database update failed")
        raise HTTPException(status_code=500, detail="GST cache batch database update failed")

    return {"updated": len(prepared), "ids": ids, "failed_records": rejected}


@router.get("/gst-cache/{record_uuid}")
async def get_row(record_uuid: UUID = Path(description='UUID from "Automation"."gstFetch".id')) -> dict[str, Any]:
    row = await prisma.gstfetch.find_unique(where={"id": str(record_uuid)})
    if row is None:
        raise HTTPException(status_code=404, detail="GST cache row not found")
    return serialize_record(row)


@router.get("/gst-cache/{record_uuid}/{state_name}/{district_name}")
async def get_row_by_location(
    record_uuid: UUID = Path(description='UUID from "Automation"."gstFetch".id'),
    state_name: str = Path(description="State name"),
    district_name: str = Path(description="District name"),
) -> dict[str, Any]:
    row = await prisma.gstfetch.find_unique(where={"id": str(record_uuid)})
    if row and ((row.stateName or "").casefold() != state_name.casefold() or (row.districtName or "").casefold() != district_name.casefold()):
        row = None

    if row is None:
        raise HTTPException(status_code=404, detail="GST cache row or location not found")
    return serialize_record(row)


@router.get("/gst-cache/{state_name}/{district_name}")
async def get_rows_by_location(
    state_name: str = Path(description="State name"),
    district_name: str = Path(description="District name"),
) -> list[dict[str, Any]]:
    candidates = await prisma.gstfetch.find_many()
    rows = [row for row in candidates if (row.stateName or "").casefold() == state_name.casefold() and (row.districtName or "").casefold() == district_name.casefold()]

    if not rows:
        raise HTTPException(status_code=404, detail="No GST cache rows found for location")
    return [serialize_record(row) for row in rows]


@router.get("/gst-cache/{record_uuid}/{column_name}")
async def get_column(
    record_uuid: UUID = Path(description='UUID from "Automation"."gstFetch".id'),
    column_name: str = Path(description="Allowed column name"),
) -> dict[str, Any]:
    if column_name not in ALLOWED_COLUMNS:
        raise HTTPException(status_code=400, detail="Invalid or unsupported column name")

    row = await prisma.gstfetch.find_unique(where={"id": str(record_uuid)})

    if row is None:
        raise HTTPException(status_code=404, detail="GST cache row not found")
    value = serialize_record(row).get(column_name)
    return {"uuid": str(record_uuid), "column": column_name, "value": value}
