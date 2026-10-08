"""Run GST Evidence without storing portal credentials in Temporal history."""

import json
import calendar
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import requests
from temporalio import activity


RUNNER = Path(__file__).resolve().parents[2] / "worker/gstEvidence/gstEvidence.py"
GSTIN_PATTERN = re.compile(r"[0-9A-Z]{15}")
FORM_NAMES = {
    "GSTR1": "gstr-1",
    "GSTR1A": "gstr-1a",
    "GSTR2B": "gstr-2b",
    "GSTR2A": "gstr-2a",
    "GSTR3B": "gstr-3b",
    "GSTR6": "gstr-6",
    "GSTR6A": "gstr-6a",
}


def _credentials() -> dict[str, tuple[str, str]]:
    base_url = os.environ["INFISICAL_API_URL"].rstrip("/")
    project = os.environ["INFISICAL_PROJECT_ID"]
    environment = os.environ["INFISICAL_ENVIRONMENT"]
    token = os.getenv("INFISICAL_TOKEN")
    if not token:
        login = requests.post(
            f"{base_url}/api/v1/auth/universal-auth/login",
            json={
                "clientId": os.environ["INFISICAL_CLIENT_ID"],
                "clientSecret": os.environ["INFISICAL_CLIENT_SECRET"],
            },
            timeout=20,
        )
        login.raise_for_status()
        token = login.json()["accessToken"]
    response = requests.get(
        f"{base_url}/api/v3/secrets/raw",
        params={
            "workspaceId": project,
            "environment": environment,
            "secretPath": "/",
            "recursive": "true",
            "include_imports": "false",
            "viewSecretValue": "true",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    items = response.json().get("secrets")
    if not isinstance(items, list):
        raise RuntimeError("Infisical returned no GST-CRED secrets array")

    credentials = {}
    for item in items:
        if str(item.get("secretPath", "")).upper() != "/GST-CRED":
            continue
        raw = item.get("secretValue")
        if not isinstance(raw, str) or not raw.strip().startswith("{"):
            continue  # A GSTIN-only secret cannot log in to the portal.
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid credential JSON for {item.get('secretKey')}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"Credential for {item.get('secretKey')} is not a JSON object")
        gstin = str(value.get("gst_no", "")).strip().upper()
        username = value.get("user_name")
        password = value.get("password")
        if not GSTIN_PATTERN.fullmatch(gstin) or not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
            raise RuntimeError(f"Incomplete GST credential for {item.get('secretKey')}")
        if gstin in credentials:
            raise RuntimeError(f"Duplicate GST credential for {gstin}")
        credentials[gstin] = (username.strip(), password)
    if not credentials:
        raise RuntimeError("No complete GST credentials found in Infisical /GST-CRED")
    return credentials


@activity.defn
def list_gst_evidence_gstins() -> list[str]:
    gstins = sorted(_credentials())
    activity.logger.info("Loaded %s GST Evidence credential identities", len(gstins))
    return gstins


def _index_uploaded_files(gstin: str, keys: list[str]) -> int:
    """Group uploaded S3 keys by return period and persist their API metadata."""
    records: dict[tuple[int, int], dict[str, object]] = {}
    pattern = re.compile(
        rf"/gstEvidence/(20\d{{2}})/(0[1-9]|1[0-2])/{gstin}/([^/]+)_({gstin})_(20\d{{2}})(0[1-9]|1[0-2])_\(\d{{4}}-\d{{2}}-\d{{2}}\)\.pdf$",
        re.IGNORECASE,
    )
    for key in keys:
        match = pattern.search(key)
        if not match:
            raise RuntimeError(f"Could not parse GST Evidence S3 key: {key}")
        year, month_number = int(match.group(1)), int(match.group(2))
        form = FORM_NAMES.get(match.group(3).upper())
        if not form or int(match.group(5)) != year or int(match.group(6)) != month_number:
            raise RuntimeError(f"GST Evidence S3 key has inconsistent period metadata: {key}")
        period = records.setdefault((year, month_number), {
            "gstin": gstin,
            "company": os.getenv("GST_EVIDENCE_COMPANY", "zetwerk"),
            "year": year,
            "month_number": month_number,
            "month": calendar.month_name[month_number].casefold(),
            "financial_year": (
                f"{year}-{str(year + 1)[-2:]}" if month_number >= 4
                else f"{year - 1}-{str(year)[-2:]}"
            ),
            "forms": [],
            "s3_paths": [],
        })
        if form not in period["forms"]:
            period["forms"].append(form)
            period["s3_paths"].append(key.rsplit("/", 1)[0])
    if not records:
        return 0

    api_url = os.getenv("GST_EVIDENCE_API_URL", "http://complaince-api-internal:8000").rstrip("/")
    response = requests.post(
        f"{api_url}/api/v1/internal/gst-evidence/batch",
        json={"records": list(records.values())},
        timeout=90,
    )
    if not response.ok:
        try:
            detail = response.json().get("detail", "no error detail")
        except ValueError:
            detail = "no error detail"
        raise RuntimeError(f"GST Evidence database update failed ({response.status_code}): {detail}")
    return sum(len(record["forms"]) for record in records.values())


@activity.defn
def run_gst_evidence(gstin: str) -> dict[str, str | int]:
    if not GSTIN_PATTERN.fullmatch(gstin):
        raise ValueError("Invalid GSTIN")
    credential = _credentials().get(gstin)
    if credential is None:
        raise RuntimeError(f"No Infisical GST credential for {gstin}")
    if not RUNNER.is_file():
        raise RuntimeError(f"GST Evidence runner is missing: {RUNNER}")

    environment = os.environ.copy()
    environment.update({
        "GST_USERNAME": credential[0],
        "GST_PASSWORD": credential[1],
        "GST_EXPECTED_GSTIN": gstin,
    })
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="gst-evidence-") as download_dir, tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
        environment["DOWNLOAD_PATH"] = download_dir
        process = subprocess.Popen(
            [sys.executable, str(RUNNER)],
            cwd=RUNNER.parent,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            while process.poll() is None:
                activity.heartbeat({"gstin": gstin, "elapsed_seconds": int(time.monotonic() - started)})
                if time.monotonic() - started > 6 * 60 * 60:
                    process.kill()
                    raise TimeoutError(f"GST Evidence timed out for {gstin}")
                time.sleep(15)
        except BaseException:
            process.kill()
            process.wait()
            raise
        output.seek(0)
        log = output.read()
    uploaded = [line.rsplit("Uploaded evidence to S3: ", 1)[-1] for line in log.splitlines() if line.startswith("Uploaded evidence to S3: ")]
    indexed = _index_uploaded_files(gstin, uploaded)
    if process.returncode:
        activity.logger.error("GST Evidence failed for %s: %s", gstin, log[-4000:])
        raise RuntimeError(f"GST Evidence failed for {gstin}; inspect worker logs")
    activity.logger.info("GST Evidence completed for %s: %s PDF uploads", gstin, len(uploaded))
    activity.logger.info("Indexed %s uploaded PDFs in Automation.gstEvidence", indexed)
    return {"gstin": gstin, "pdf_count": len(uploaded), "database_records": indexed}
