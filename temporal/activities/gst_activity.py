import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from temporalio import activity

load_dotenv()


GST_FETCH_API_URL = os.getenv("GST_FETCH_API_URL", "http://complaince-api-internal:8000").rstrip("/")


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

GST_BOT_DIR = PROJECT_ROOT / "worker" / "gstFactCheck"

GST_PYTHON = Path(os.getenv("GST_PYTHON", sys.executable))

GST_SCRIPT = GST_BOT_DIR / "gstbot.py"


# ============================================================
# GST BOT TIMEOUT
# ============================================================

GST_BOT_TIMEOUT_SECONDS = 30 * 60


# ============================================================
# GET GST RECORDS
# ============================================================

@activity.defn
def sync_gst_records() -> dict[str, int]:
    """Make Infisical project secrets the source of truth before each run."""
    response = requests.post(f"{GST_FETCH_API_URL}/api/v1/internal/gst-cache/sync", timeout=90)
    if not response.ok:
        try:
            detail = response.json().get("detail", "no error detail")
        except ValueError:
            detail = "no error detail"
        raise RuntimeError(f"GST secret sync failed ({response.status_code}): {detail}")
    result = response.json()
    activity.logger.info("GST secret sync complete: %s active, %s deactivated", result["active"], result["deactivated"])
    return result


@activity.defn
def get_gst_records() -> list[dict[str, Any]]:
    """Get synced GSTINs and row IDs from the internal API."""

    activity.logger.info(
        "Loading GST records through GST Fetch API..."
    )

    try:
        response = requests.get(
            f"{GST_FETCH_API_URL}/api/v1/gst-cache/records",
            timeout=30,
        )
        response.raise_for_status()
        rows = response.json()
    except requests.RequestException as exc:

        activity.logger.error(
            f"GST Fetch API failed to load records: {exc}"
        )

        raise

    gst_records = []
    for row in rows:
        record_id = row.get("id")
        gstin = row.get("gstin")
        if not record_id or not isinstance(gstin, str) or len(gstin) != 15:
            raise RuntimeError("GST Fetch API returned an invalid synced record")
        gst_records.append({"id": str(record_id), "gstin": gstin})

    activity.logger.info(
        f"Loaded {len(gst_records)} GST records."
    )

    return gst_records


# ============================================================
# RUN GST BOT
# ============================================================

@activity.defn
def run_gst_bot(
    record_id: str,
    gstin: str,
) -> str:
    """
    Run the existing GST bot for one GSTIN.
    """

    gstin = gstin.strip().upper()

    activity.logger.info(
        f"Starting GST bot | "
        f"ID={record_id} | "
        f"GSTIN={gstin}"
    )

    # --------------------------------------------------------
    # Validate GSTIN
    # --------------------------------------------------------

    if len(gstin) != 15:

        raise ValueError(
            f"Invalid GSTIN: {gstin}"
        )

    # --------------------------------------------------------
    # Check Python executable
    # --------------------------------------------------------

    if not GST_PYTHON.exists():

        raise RuntimeError(
            f"Python executable not found: "
            f"{GST_PYTHON}"
        )

    # --------------------------------------------------------
    # Check GST script
    # --------------------------------------------------------

    if not GST_SCRIPT.exists():

        raise RuntimeError(
            f"GST bot script not found: "
            f"{GST_SCRIPT}"
        )

    # --------------------------------------------------------
    # Command
    # --------------------------------------------------------

    command = [
        str(GST_PYTHON),
        str(GST_SCRIPT),
        gstin,
        "--headless",
    ]

    activity.logger.info(
        f"Executing GST bot | "
        f"ID={record_id}"
    )

    # --------------------------------------------------------
    # Run bot
    # --------------------------------------------------------

    try:

        result = subprocess.run(
            command,
            cwd=str(GST_BOT_DIR),
            capture_output=True,
            text=True,
            timeout=GST_BOT_TIMEOUT_SECONDS,
        )

    except subprocess.TimeoutExpired as exc:

        activity.logger.error(
            f"GST bot timed out | "
            f"ID={record_id} | "
            f"GSTIN={gstin}"
        )

        raise RuntimeError(
            f"GST bot timed out for {gstin}"
        ) from exc

    # --------------------------------------------------------
    # Check return code
    # --------------------------------------------------------

    if result.returncode != 0:

        activity.logger.error(
            f"GST bot failed | "
            f"ID={record_id} | "
            f"GSTIN={gstin} | "
            f"Exit code={result.returncode}"
        )

        if result.stderr:

            activity.logger.error(
                f"GST bot stderr:\n"
                f"{result.stderr[-4000:]}"
            )

        raise RuntimeError(
            f"GST bot failed for {gstin}"
        )

    # --------------------------------------------------------
    # Log output
    # --------------------------------------------------------

    if result.stdout:

        activity.logger.info(
            f"GST bot output | "
            f"ID={record_id}:\n"
            f"{result.stdout[-4000:]}"
        )

    # --------------------------------------------------------
    # Expected JSON
    # --------------------------------------------------------

    output_file = (
        GST_BOT_DIR
        / f"{gstin}.json"
    )

    if not output_file.exists():

        raise RuntimeError(
            f"GST bot completed but output file "
            f"was not found: {output_file}"
        )

    activity.logger.info(
        f"GST bot completed successfully | "
        f"ID={record_id} | "
        f"GSTIN={gstin}"
    )

    return str(output_file)


# ============================================================
# UPDATE DATABASE
# ============================================================

@activity.defn
def update_gst_database(
    record_id: str,
    gstin: str,
    output_file: str,
) -> str:
    """Send the GST result to the persistence API."""
    gstin = gstin.strip().upper()
    json_path = Path(output_file)
    if not json_path.exists():
        raise RuntimeError(f"GST JSON file not found: {json_path}")

    try:
        with json_path.open("r", encoding="utf-8") as file:
            gst_data = json.load(file)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON generated for {gstin}") from exc

    json_gstin = str(gst_data.get("gst_number", "")).strip().upper()
    if json_gstin and json_gstin != gstin:
        raise RuntimeError(f"GSTIN mismatch: expected {gstin}, but JSON contains {json_gstin}")

    try:
        response = requests.put(
            f"{GST_FETCH_API_URL}/api/v1/gst-cache/{record_id}",
            json=gst_data,
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        activity.logger.error(
            f"GST Fetch API failed to update ID={record_id}, GSTIN={gstin}: {exc}"
        )
        raise

    activity.logger.info(f"GST result persisted through API | ID={record_id} | GSTIN={gstin}")
    return f"Database updated successfully for ID={record_id}"


@activity.defn
def run_and_persist_gst_batch(records: list[dict[str, str]]) -> dict[str, Any]:
    """Scrape up to four GSTINs and persist their results with one DB transaction."""
    if not records or len(records) > 4:
        raise ValueError("GST batch must contain between 1 and 4 records")

    successful: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    marker = "__GST_RESULT_JSON__"

    for record in records:
        record_id = str(record["id"])
        gstin = str(record["gstin"]).strip().upper()
        if len(gstin) != 15:
            failed.append({"id": record_id, "gstin": gstin, "error": "Invalid GSTIN"})
            continue

        command = [str(GST_PYTHON), str(GST_SCRIPT), gstin, "--headless", "--stdout-json"]
        try:
            result = subprocess.run(
                command,
                cwd=str(GST_BOT_DIR),
                capture_output=True,
                text=True,
                timeout=GST_BOT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            failed.append({"id": record_id, "gstin": gstin, "error": "GST bot timed out"})
            continue

        result_line = next((line[len(marker):] for line in reversed(result.stdout.splitlines()) if line.startswith(marker)), None)
        if result.returncode != 0 or result_line is None:
            activity.logger.error("GST bot failed for %s (exit=%s): %s", gstin, result.returncode, result.stderr[-2000:])
            failed.append({"id": record_id, "gstin": gstin, "error": "GST bot failed to produce a successful result"})
            continue

        try:
            gst_data = json.loads(result_line)
        except json.JSONDecodeError:
            failed.append({"id": record_id, "gstin": gstin, "error": "GST bot returned invalid JSON"})
            continue

        if str(gst_data.get("gst_number", "")).strip().upper() != gstin:
            failed.append({"id": record_id, "gstin": gstin, "error": "GST bot result GSTIN mismatch"})
            continue
        successful.append({"id": record_id, "gst_data": gst_data})

    persisted: dict[str, Any] = {"ids": [], "failed_records": []}
    if successful:
        try:
            response = requests.post(
                f"{GST_FETCH_API_URL}/api/v1/internal/gst-cache/batch",
                json={"records": successful},
                timeout=60,
            )
            response.raise_for_status()
            persisted = response.json()
        except requests.RequestException as exc:
            detail = ""
            if exc.response is not None:
                try:
                    detail = str(exc.response.json().get("detail", ""))
                except ValueError:
                    detail = ""
            activity.logger.exception("GST batch database persistence failed")
            raise RuntimeError(f"GST batch persistence failed: {detail or type(exc).__name__}") from exc

    failed.extend(persisted.get("failed_records", []))
    persisted_ids = set(persisted.get("ids", []))
    successful_records = [
        {"id": item["id"], "gstin": item["gst_data"]["gst_number"]}
        for item in successful if item["id"] in persisted_ids
    ]
    return {
        "successful": len(successful_records),
        "failed": len(failed),
        "successful_records": successful_records,
        "failed_records": failed,
    }
