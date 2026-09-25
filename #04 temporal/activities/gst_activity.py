import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import psycopg
import requests
from dotenv import load_dotenv
from psycopg.types.json import Json, Jsonb
from temporalio import activity

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


# ============================================================
# POSTGRESQL CONFIGURATION
# ============================================================
# For now, DB details are kept directly in this Python file.
# Do NOT commit this file with real credentials to Git.

DB_HOST = os.getenv("DB_HOST", "192.168.10.100")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "complaince")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

GST_BOT_DIR = PROJECT_ROOT / "#02 fact check" / "gstbot"

GST_PYTHON = Path(os.getenv("GST_PYTHON", sys.executable))

GST_SCRIPT = GST_BOT_DIR / "gstbot.py"


# ============================================================
# DATABASE
# ============================================================

GST_SCHEMA = "gstfetch"
GST_TABLE = "gstin_cache"


# ============================================================
# GST BOT TIMEOUT
# ============================================================

GST_BOT_TIMEOUT_SECONDS = 30 * 60


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_database_connection():
    """
    Create a PostgreSQL connection.
    """

    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


# ============================================================
# GET GST RECORDS
# ============================================================

@activity.defn
def get_gst_records() -> list[dict[str, Any]]:
    """
    Get GST record IDs from PostgreSQL.

    PostgreSQL ID is a UUID/string.

    Example:

        PostgreSQL:
            id =
            0342ea1d-78f8-43c2-b112-f13e88774733

        Infisical:
            secret name =
            0342ea1d-78f8-43c2-b112-f13e88774733

        Infisical secret value =
            GSTIN
    """

    activity.logger.info(
        "Loading GST records from PostgreSQL..."
    )

    query = f"""
        SELECT id
        FROM {GST_SCHEMA}.{GST_TABLE}
        ORDER BY id
    """

    try:

        with get_database_connection() as conn:

            with conn.cursor() as cursor:

                cursor.execute(query)

                rows = cursor.fetchall()

    except Exception as exc:

        activity.logger.error(
            f"Failed to load GST records: {exc}"
        )

        raise

    gst_records = []

    for row in rows:

        record_id = row[0]

        # ----------------------------------------------------
        # Validate ID
        # ----------------------------------------------------

        if record_id is None:

            raise RuntimeError(
                "Found GST record with NULL id."
            )

        # ----------------------------------------------------
        # IMPORTANT:
        # PostgreSQL UUID -> Python string
        # ----------------------------------------------------

        record_id = str(record_id)

        # ----------------------------------------------------
        # ID is also the Infisical secret name
        # ----------------------------------------------------

        secret_name = record_id

        gstin = os.getenv(secret_name)
        if not gstin and os.getenv("INFISICAL_TOKEN"):
            api_url = os.getenv("INFISICAL_API_URL", "http://infisical:8080")
            headers = {"Authorization": f"Bearer {os.environ['INFISICAL_TOKEN']}"}
            response = requests.get(
                f"{api_url}/api/v4/secrets/{secret_name}",
                params={"projectId": os.environ["INFISICAL_PROJECT_ID"], "environment": os.getenv("INFISICAL_ENVIRONMENT", "dev"), "secretPath": "/"},
                headers=headers,
                timeout=15,
            )
            if response.status_code == 404:
                response = requests.get(
                    f"{api_url}/api/v3/secrets/raw/{secret_name}",
                    params={"workspaceId": os.environ["INFISICAL_PROJECT_ID"], "environment": os.getenv("INFISICAL_ENVIRONMENT", "dev"), "secretPath": "/"},
                    headers=headers,
                    timeout=15,
                )
            if response.status_code == 404:
                activity.logger.warning(
                    f"Skipping UUID {record_id}: no Infisical secret found."
                )
                continue
            response.raise_for_status()
            gstin = response.json()["secret"]["secretValue"]

        if not gstin:

            activity.logger.warning(
                f"Skipping UUID {record_id}: Infisical secret '{secret_name}' was not found."
            )
            continue

        gstin = gstin.strip().upper()

        # ----------------------------------------------------
        # Validate GSTIN
        # ----------------------------------------------------

        if len(gstin) != 15:

            raise RuntimeError(
                f"Invalid GSTIN for ID "
                f"{record_id}: {gstin}"
            )

        # ----------------------------------------------------
        # Add record
        # ----------------------------------------------------

        gst_records.append(
            {
                "id": record_id,
                "gstin": gstin,
            }
        )

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
# GET DATABASE COLUMN TYPES
# ============================================================

def get_column_types(conn) -> dict[str, str]:
    """
    Get PostgreSQL data types for:
        business_info
        filing_tables
    """

    query = """
        SELECT
            column_name,
            data_type
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
          AND column_name IN (
              'business_info',
              'filing_tables'
          )
    """

    with conn.cursor() as cursor:

        cursor.execute(
            query,
            (
                GST_SCHEMA,
                GST_TABLE,
            ),
        )

        rows = cursor.fetchall()

    return {
        row[0]: row[1]
        for row in rows
    }


# ============================================================
# PREPARE JSON VALUE
# ============================================================

def prepare_json_value(
    value: Any,
    column_type: str | None,
):
    """
    Convert Python JSON data into a format
    PostgreSQL can store.
    """

    if value is None:

        return None

    if column_type == "json":

        return Json(value)

    if column_type == "jsonb":

        return Jsonb(value)

    return json.dumps(
        value,
        ensure_ascii=False,
    )


# ============================================================
# UPDATE DATABASE
# ============================================================

@activity.defn
def update_gst_database(
    record_id: str,
    gstin: str,
    output_file: str,
) -> str:
    """
    Read the GST JSON file and update
    the corresponding PostgreSQL record.
    """

    gstin = gstin.strip().upper()

    activity.logger.info(
        f"Updating PostgreSQL | "
        f"ID={record_id} | "
        f"GSTIN={gstin}"
    )

    json_path = Path(output_file)

    # --------------------------------------------------------
    # Check JSON file
    # --------------------------------------------------------

    if not json_path.exists():

        raise RuntimeError(
            f"GST JSON file not found: "
            f"{json_path}"
        )

    # --------------------------------------------------------
    # Read JSON
    # --------------------------------------------------------

    try:

        with open(
            json_path,
            "r",
            encoding="utf-8",
        ) as file:

            gst_data = json.load(file)

    except json.JSONDecodeError as exc:

        raise RuntimeError(
            f"Invalid JSON generated for {gstin}"
        ) from exc

    # --------------------------------------------------------
    # Extract fields
    # --------------------------------------------------------

    business_info = gst_data.get("business_info")
    filing_tables = gst_data.get("filing_tables") or {}

    if not isinstance(business_info, dict) or not business_info:
        raise RuntimeError(f"business_info missing in GST JSON for {gstin}")

    json_gstin = str(gst_data.get("gst_number", "")).strip().upper()
    if json_gstin and json_gstin != gstin:
        raise RuntimeError(
            f"GSTIN mismatch: expected {gstin}, but JSON contains {json_gstin}"
        )

    business_info = dict(business_info)
    return_filing_frequency = business_info.pop("Return Filing Frequency", None)
    business_info = {"GSTIN": gstin, **business_info}
    filing_tables = {
        "Return Filing Frequency": return_filing_frequency,
        **filing_tables,
    }

    legal_name = business_info.get("Legal Name of Business") or gst_data.get("Trade Name")
    status = business_info.get("GSTIN / UIN  Status") or gst_data.get("GSTIN / UIN  Status")
    if not legal_name:
        raise RuntimeError(f"Legal Name of Business missing for {gstin}")
    if not status:
        raise RuntimeError(f"GSTIN / UIN Status missing for {gstin}")

    address = str(business_info.get("Principal Place of Business") or "")
    location_match = re.search(
        r",\s*([^,]+),\s*([^,]+),\s*\d{6}\s*$",
        address,
    )
    district_name = location_match.group(1).strip() if location_match else None
    state_name = location_match.group(2).strip() if location_match else None

    # --------------------------------------------------------
    # Connect to PostgreSQL
    # --------------------------------------------------------

    try:

        with get_database_connection() as conn:

            # ------------------------------------------------
            # Get column types
            # ------------------------------------------------

            column_types = get_column_types(
                conn
            )

            # ------------------------------------------------
            # Prepare JSON values
            # ------------------------------------------------

            business_info_value = (
                prepare_json_value(
                    business_info,
                    column_types.get(
                        "business_info"
                    ),
                )
            )

            filing_tables_value = (
                prepare_json_value(
                    filing_tables,
                    column_types.get(
                        "filing_tables"
                    ),
                )
            )

            # ------------------------------------------------
            # Update record
            # ------------------------------------------------

            update_query = f"""
                UPDATE {GST_SCHEMA}.{GST_TABLE}
                SET
                    gstin = %s,
                    business_info = %s,
                    filing_tables = %s,
                    legal_name = %s,
                    status = %s,
                    state_name = %s,
                    district_name = %s,
                    fetched_at = CURRENT_TIMESTAMP,
                    fetch_count =
                        COALESCE(fetch_count, 0) + 1
                WHERE id = %s
            """

            with conn.cursor() as cursor:

                cursor.execute(
                    update_query,
                    (
                        gstin,
                        business_info_value,
                        filing_tables_value,
                        legal_name,
                        status,
                        state_name,
                        district_name,
                        record_id,
                    ),
                )

                # --------------------------------------------
                # Make sure record exists
                # --------------------------------------------

                if cursor.rowcount == 0:

                    raise RuntimeError(
                        f"No GST record found "
                        f"for ID {record_id}"
                    )

            conn.commit()

    except Exception as exc:

        activity.logger.error(
            f"PostgreSQL update failed | "
            f"ID={record_id} | "
            f"GSTIN={gstin} | "
            f"Error={exc}"
        )

        raise

    activity.logger.info(
        f"PostgreSQL updated successfully | "
        f"ID={record_id} | "
        f"GSTIN={gstin}"
    )

    return (
        f"Database updated successfully "
        f"for ID={record_id}"
    )
