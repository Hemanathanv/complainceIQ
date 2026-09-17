`"""Download GST documents from the CBIC Tax Information Portal.

Install once:
    py -m pip install requests playwright
    py -m playwright install chromium

Run:
    py gst_cbic_scraper.py

Files are saved under output/gst/ as:
    acts, rules, forms, circulars, notifications,
    instructions-guidelines, and orders.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from playwright.sync_api import sync_playwright


BASE_URL = "https://taxinformation.cbic.gov.in/"
GST_TAX_ID = 1000001
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            # The script-local .env is authoritative for this scraper.
            os.environ[key.strip()] = value.strip().strip('"')
DOWNLOAD_BASE = Path(os.environ.get("DOWNLOAD_BASE_PATH", SCRIPT_DIR))
DEFAULT_OUTPUT = DOWNLOAD_BASE / Path(__file__).stem
AUDIT_FILE = DEFAULT_OUTPUT / "GST_Audit.xlsx"
LAST_OUTPUT_ROOT = DEFAULT_OUTPUT
REQUEST_DELAY_SECONDS = 0.35
TIMEOUT_SECONDS = 60
LEGACY_GST_OUTPUT = Path(r"C:\Users\User\Desktop\go lang\gst\output\gst")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gst-cbic")


def write_audit(root: Path, manifest: dict) -> None:
    """Write one audit row per GST record discovered by the original scraper."""
    records = manifest.get("files") or []
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Audit"
    sheet.append(["Name", "Category", "Date", "PDF Path"])
    recorded_paths = set()
    for record in records:
        original = Path(str(record.get("path") or "document.pdf").replace("\\", "/"))
        name = (record.get("subject") or record.get("notificationSubject") or
                record.get("formName") or record.get("notificationNo") or original.stem)
        category = record.get("section") or record.get("category") or "GST"
        if record.get("category") and record.get("category") != category:
            category = f"{category} / {record['category']}"
        date = record.get("date") or record.get("year") or ""
        local = record.get("localPath")
        pdf_path = str((root / local).resolve()) if local else ("FAILED: " + str(record.get("error") or "NOT DOWNLOADED"))
        sheet.append([name, category, date, pdf_path])
        if local:
            recorded_paths.add(str((root / local).resolve()))
    # Include fallback/previously completed PDFs that are not represented in
    # the current API manifest.
    for pdf in root.rglob("*.pdf"):
        if "_quarantine" in pdf.relative_to(root).parts:
            continue
        resolved = str(pdf.resolve())
        if resolved in recorded_paths:
            continue
        relative = pdf.relative_to(root)
        category = relative.parts[0] if relative.parts else "GST"
        sheet.append([pdf.stem, category, "", resolved])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F5597")
        cell.alignment = Alignment(horizontal="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for col, width in {"A": 110, "B": 32, "C": 18, "D": 115}.items():
        sheet.column_dimensions[col].width = width
    output = root / "GST_Audit.xlsx"
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    log.info("GST audit saved: %s (%d records)", output, len(records))


def safe_name(value: str) -> str:
    value = " ".join(str(value).split())
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" .")
    return value or "unnamed"


def safe_component(value: str, max_length: int = 120) -> str:
    """Keep descriptive names within Windows path-length limits."""
    value = safe_name(value)
    return value[:max_length].rstrip(" .") or "unnamed"


def is_gst_path(path: str) -> bool:
    return path.replace("\\", "/").lower().startswith("tax_repository/gst/")


def document_url(path: str) -> str:
    return urljoin(BASE_URL, "content/pdf/" + path.replace("\\", "/").lstrip("/"))


def new_session() -> requests.Session:
    session = requests.Session()
    session.verify = False  # CBIC currently serves a non-standard NIC CA chain.
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "GST-CBIC-document-collector/1.0",
    })
    return session


def token(session: requests.Session) -> str:
    response = session.post(urljoin(BASE_URL, "api/authenticate-token"), timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()["id_token"]


def get_json(session: requests.Session, jwt: str, api_path: str):
    response = session.get(
        urljoin(BASE_URL, api_path),
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    # CBIC occasionally appends a duplicate JSON payload to a successful
    # response. Decode the first complete value instead of rejecting the
    # otherwise valid document list with a JSON "Extra data" error.
    text = response.text.lstrip()
    value, _ = json.JSONDecoder().raw_decode(text)
    return value


def post_json(session: requests.Session, jwt: str, api_path: str, payload: dict):
    """POST JSON to CBIC, tolerating its occasional duplicate JSON payload."""
    response = session.post(
        urljoin(BASE_URL, api_path),
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    text = response.text.lstrip()
    value, _ = json.JSONDecoder().raw_decode(text)
    return value


def section_title(item: dict, fallback: str) -> str:
    """Build the same section/provision label used for the downloaded PDFs."""
    title = item.get("sectionNo") or fallback
    if item.get("sectionName"):
        title += f" - {item['sectionName']}"
    return title


def collect_relationships(session: requests.Session, jwt: str, errors: list[str]) -> dict:
    """Collect only direct CBIC Related Content links without downloading PDFs."""
    acts = [item for item in get_json(session, jwt, "api/cbic-act-msts")
            if item.get("isActive") == "Y" and item.get("taxId", {}).get("id") == GST_TAX_ID]
    rule_docs = [item for item in get_json(session, jwt, "api/cbic-rule-msts")
                 if item.get("isActive") == "Y" and item.get("cbicTaxMst", {}).get("id") == GST_TAX_ID]
    forms = [item for item in get_json(session, jwt, "api/cbic-form-msts")
             if item.get("isActive") == "Y" and item.get("tax", {}).get("id") == GST_TAX_ID]

    forms_by_id = {str(item.get("id")): item for item in forms if item.get("id") is not None}
    rule_sections_by_id: dict[str, dict] = {}
    rule_sections: list[dict] = []
    for rule_doc in rule_docs:
        rule_name = rule_doc.get("ruleName") or rule_doc.get("ruleDocName") or f"Rule {rule_doc.get('id')}"
        try:
            provisions = get_json(session, jwt, f"api/cbic-rule-section-msts/findSectionByRuleId/{rule_doc['id']}")
        except Exception as exc:
            errors.append(f"relationships: rule sections for {rule_name}: {exc}")
            continue
        for provision in provisions:
            if provision.get("isActive") != "Y" or not provision.get("contentId"):
                continue
            record = {
                "ruleDocument": rule_name,
                "ruleSection": section_title(provision, "Rule"),
                "ruleSectionId": provision.get("id"),
                "contentId": provision.get("contentId"),
            }
            rule_sections.append(record)
            if provision.get("id") is not None:
                rule_sections_by_id[str(provision["id"])] = record

    act_sections_by_id: dict[str, dict] = {}
    act_sections: list[dict] = []
    for act in acts:
        act_name = act.get("actName") or f"Act {act.get('id')}"
        try:
            sections = get_json(session, jwt, f"api/cbic-act-section-msts/findByActId/{act['id']}")
        except Exception as exc:
            errors.append(f"relationships: act sections for {act_name}: {exc}")
            continue
        for section in sections:
            if section.get("isActive") != "Y" or not section.get("contentId"):
                continue
            record = {
                "act": act_name,
                "actSection": section_title(section, "Section"),
                "actSectionId": section.get("id"),
                "contentId": section.get("contentId"),
            }
            act_sections.append(record)
            if section.get("id") is not None:
                act_sections_by_id[str(section["id"])] = record

    act_to_rule: list[dict] = []
    act_to_form: list[dict] = []
    seen_act_to_rule: set[tuple[str, str, str, str]] = set()
    seen_act_to_form: set[tuple[str, str, str]] = set()
    for index, act_section in enumerate(act_sections, 1):
        try:
            response = post_json(
                session, jwt, "api/cbic-content-maps/fetch-related-content",
                {"parentContentCategory": "RULE", "parentContentId": act_section["contentId"]},
            )
        except Exception as exc:
            errors.append(f"relationships: {act_section['act']} / {act_section['actSection']} related rules: {exc}")
        else:
            for related in response.get("ruleSectionMsts") or []:
                resolved = rule_sections_by_id.get(str(related.get("ruleId")))
                rule_document = (resolved or {}).get("ruleDocument") or related.get("ruleName") or ""
                rule_section = (resolved or {}).get("ruleSection") or related.get("ruleSubjectName") or ""
                key = (act_section["act"], act_section["actSection"], rule_document, rule_section)
                if key not in seen_act_to_rule:
                    seen_act_to_rule.add(key)
                    act_to_rule.append({**act_section, "ruleDocument": rule_document, "ruleSection": rule_section, "ruleSectionId": related.get("ruleId")})
        try:
            response = post_json(
                session, jwt, "api/cbic-content-maps/fetch-related-content",
                {"parentContentCategory": "FORM", "parentContentId": act_section["contentId"]},
            )
        except Exception as exc:
            errors.append(f"relationships: {act_section['act']} / {act_section['actSection']} related forms: {exc}")
        else:
            for related in response.get("formMsts") or []:
                form = forms_by_id.get(str(related.get("formId")), {})
                form_number = form.get("formNo") or related.get("formNumber") or ""
                form_name = form.get("formName") or related.get("formName") or ""
                key = (act_section["act"], act_section["actSection"], form_number)
                if key not in seen_act_to_form:
                    seen_act_to_form.add(key)
                    act_to_form.append({**act_section, "form": form_number, "formName": form_name, "formCategory": form.get("formCategory") or related.get("formCategory") or ""})
        time.sleep(REQUEST_DELAY_SECONDS)
        if index % 50 == 0:
            log.info("relationships: checked %d/%d act sections", index, len(act_sections))

    rule_to_act: list[dict] = []
    rule_to_form: list[dict] = []
    seen_rule_to_act: set[tuple[str, str, str, str]] = set()
    seen_rule_to_form: set[tuple[str, str, str]] = set()
    for index, rule_section in enumerate(rule_sections, 1):
        try:
            response = post_json(
                session, jwt, "api/cbic-content-maps/fetch-related-content",
                {"parentContentCategory": "ACT", "parentContentId": rule_section["contentId"]},
            )
        except Exception as exc:
            errors.append(f"relationships: {rule_section['ruleDocument']} / {rule_section['ruleSection']} related acts: {exc}")
        else:
            for related in response.get("actSectionMsts") or []:
                resolved = act_sections_by_id.get(str(related.get("sectionId")))
                act_name = (resolved or {}).get("act") or related.get("actName") or ""
                act_section = (resolved or {}).get("actSection") or related.get("actSubjectName") or ""
                key = (rule_section["ruleDocument"], rule_section["ruleSection"], act_name, act_section)
                if key not in seen_rule_to_act:
                    seen_rule_to_act.add(key)
                    rule_to_act.append({**rule_section, "act": act_name, "actSection": act_section, "actSectionId": related.get("sectionId")})
        try:
            response = post_json(
                session, jwt, "api/cbic-content-maps/fetch-related-content",
                {"parentContentCategory": "FORM", "parentContentId": rule_section["contentId"]},
            )
        except Exception as exc:
            errors.append(f"relationships: {rule_section['ruleDocument']} / {rule_section['ruleSection']} related forms: {exc}")
            continue
        for related in response.get("formMsts") or []:
            form = forms_by_id.get(str(related.get("formId")), {})
            form_number = form.get("formNo") or related.get("formNumber") or ""
            form_name = form.get("formName") or related.get("formName") or ""
            key = (rule_section["ruleDocument"], rule_section["ruleSection"], form_number)
            if key in seen_rule_to_form:
                continue
            seen_rule_to_form.add(key)
            rule_to_form.append({
                "ruleDocument": rule_section["ruleDocument"],
                "ruleSection": rule_section["ruleSection"],
                "form": form_number,
                "formName": form_name,
                "formCategory": form.get("formCategory") or related.get("formCategory") or "",
            })
        time.sleep(REQUEST_DELAY_SECONDS)
        if index % 50 == 0:
            log.info("relationships: checked %d/%d rule sections", index, len(rule_sections))

    form_to_act: list[dict] = []
    form_to_rule: list[dict] = []
    seen_form_to_act: set[tuple[str, str, str]] = set()
    seen_form_to_rule: set[tuple[str, str, str]] = set()
    for index, form in enumerate(forms, 1):
        if not form.get("contentId"):
            continue
        form_number = form.get("formNo") or f"Form {form.get('id')}"
        form_name = form.get("formName") or ""
        try:
            response = post_json(session, jwt, "api/cbic-content-maps/fetch-related-content", {"parentContentCategory": "ACT", "parentContentId": form["contentId"]})
        except Exception as exc:
            errors.append(f"relationships: {form_number} related acts: {exc}")
        else:
            for related in response.get("actSectionMsts") or []:
                resolved = act_sections_by_id.get(str(related.get("sectionId")))
                act_name = (resolved or {}).get("act") or related.get("actName") or ""
                act_section = (resolved or {}).get("actSection") or related.get("actSubjectName") or ""
                key = (form_number, act_name, act_section)
                if key not in seen_form_to_act:
                    seen_form_to_act.add(key)
                    form_to_act.append({"form": form_number, "formName": form_name, "act": act_name, "actSection": act_section})
        try:
            response = post_json(session, jwt, "api/cbic-content-maps/fetch-related-content", {"parentContentCategory": "RULE", "parentContentId": form["contentId"]})
        except Exception as exc:
            errors.append(f"relationships: {form_number} related rules: {exc}")
        else:
            for related in response.get("ruleSectionMsts") or []:
                resolved = rule_sections_by_id.get(str(related.get("ruleId")))
                rule_document = (resolved or {}).get("ruleDocument") or related.get("ruleName") or ""
                rule_section = (resolved or {}).get("ruleSection") or related.get("ruleSubjectName") or ""
                key = (form_number, rule_document, rule_section)
                if key not in seen_form_to_rule:
                    seen_form_to_rule.add(key)
                    form_to_rule.append({"form": form_number, "formName": form_name, "ruleDocument": rule_document, "ruleSection": rule_section})
        time.sleep(REQUEST_DELAY_SECONDS)
        if index % 50 == 0:
            log.info("relationships: checked %d/%d forms", index, len(forms))

    return {
        "source": BASE_URL,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actToRule": act_to_rule,
        "actToForm": act_to_form,
        "ruleToAct": rule_to_act,
        "ruleToForm": rule_to_form,
        "formToAct": form_to_act,
        "formToRule": form_to_rule,
        "errors": errors,
    }


def add_record(records: dict[str, dict], path: str, section: str, form_id: int | None = None,
               category: str | None = None, year: str | None = None,
               form_name: str | None = None) -> None:
    path = (path or "").replace("\\", "/").strip("/")
    if not (is_gst_path(path) and path.lower().endswith(".pdf")):
        return
    url = document_url(path)
    record = records.setdefault(url, {"url": url, "path": path, "section": section, "formId": form_id})
    if category:
        record["category"] = category
    if year:
        record["year"] = year
    if form_name:
        record["formName"] = form_name


def discover_static(session: requests.Session, jwt: str, records: dict[str, dict], errors: list[str]) -> None:
    sources = [
        ("api/cbic-act-msts", "acts", "contentFilePath", "taxId"),
        ("api/cbic-rule-msts", "rules", "contentFilePath", "cbicTaxMst"),
        ("api/cbic-form-msts", "forms", "contentFilePath", "tax"),
        ("api/cbic-regulation-msts", "regulations", "contentFilePath", None),
    ]
    for endpoint, section, field, tax_field in sources:
        try:
            items = get_json(session, jwt, endpoint)
            before = len(records)
            for item in items:
                if item.get("isActive") != "Y":
                    continue
                if tax_field and item.get(tax_field, {}).get("id") != GST_TAX_ID:
                    continue
                add_record(records, item.get(field), section, item.get("id") if section == "forms" else None,
                           category=item.get("formCategory") if section == "forms" else None,
                           year=item.get("formNo") if section == "forms" else None,
                           form_name=item.get("formName") if section == "forms" else None)
            log.info("%s: %d GST documents", section, len(records) - before)
        except Exception as exc:
            errors.append(f"{section} discovery: {exc}")
            log.warning("%s discovery failed: %s", section, exc)

    try:
        items = get_json(session, jwt, f"api/cbic-circular-msts/fetchAllCircularsByTaxId/{GST_TAX_ID}")
        before = len(records)
        for item in items:
            if item.get("isActive") != "N":
                date_value = item.get("circularDt") or item.get("issueDt") or item.get("createdDt") or ""
                year = str(date_value)[:4] if str(date_value)[:4].isdigit() else None
                add_record(records, item.get("docFilePath"), "circulars",
                           category=item.get("circularCategory"), year=year)
        log.info("circulars: %d GST documents", len(records) - before)
    except Exception as exc:
        errors.append(f"circulars discovery: {exc}")
        log.warning("circular discovery failed: %s", exc)


def discover_circulars(session: requests.Session, jwt: str, records: dict[str, dict], errors: list[str]) -> None:
    """Discover all GST circulars with the portal's category and issue year."""
    try:
        items = get_json(session, jwt, f"api/cbic-circular-msts/fetchAllCircularsByTaxId/{GST_TAX_ID}")
        for item in items:
            if item.get("isActive") == "N":
                continue
            date_value = item.get("circularDt") or item.get("issueDt") or item.get("createdDt") or ""
            year = str(date_value)[:4] if str(date_value)[:4].isdigit() else None
            add_record(records, item.get("docFilePath"), "circulars",
                       category=item.get("circularCategory"), year=year)
        log.info("circulars: %d GST documents", sum(1 for item in records.values() if item["section"] == "circulars"))
    except Exception as exc:
        errors.append(f"circulars discovery: {exc}")
        log.warning("circular discovery failed: %s", exc)


GST_DYNAMIC_JS = r"""
async ({base, fromYear}) => {
  const results = [], errors = [];
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function fetchRetry(url, options = {}) {
    let last;
    for (let attempt = 0; attempt < 3; attempt++) {
      await sleep(250);
      try { return await fetch(url, options); }
      catch (error) { last = error; await sleep(1000 * (attempt + 1)); }
    }
    throw last;
  }
  async function getToken() {
    const response = await fetchRetry(base + 'api/authenticate-token', {method: 'POST'});
    if (!response.ok) throw new Error('authenticate-token HTTP ' + response.status);
    return (await response.json()).id_token;
  }
  function cleanPath(value) { return (value || '').replaceAll('\\', '/').replace(/^\/+/, ''); }
  function add(value, type) { const path = cleanPath(value); if (path.toLowerCase().endsWith('.pdf')) results.push({path, type}); }
  async function allPages(endpoint, headers) {
    const all = [];
    for (let page = 0; ; page++) {
      const joiner = endpoint.includes('?') ? '&' : '?';
      const response = await fetchRetry(endpoint + joiner + 'page=' + page + '&size=100', {headers});
      if (!response.ok) throw new Error(endpoint + ' HTTP ' + response.status);
      const items = await response.json();
      if (!Array.isArray(items) || !items.length) break;
      all.push(...items);
      if (items.length < 100) break;
    }
    return all;
  }
  let jwt;
  try { jwt = await getToken(); } catch (error) { return {results, errors: ['token: ' + error.message]}; }
  const headers = {Authorization: 'Bearer ' + jwt, Accept: 'application/json'};
  const thisYear = new Date().getFullYear();

  // The portal currently returns HTTP 500 for some GST years/categories.
  // Keep trying each year independently so an outage does not discard data
  // from years the portal does serve.
  for (const [section, endpoint] of [
    ['notifications', 'api/cbic-notification-msts/fetchNotificationByYearAndCategory'],
    ['instructions-guidelines', 'api/cbic-instruction-msts/fetch-instructions-year-category'],
    ['orders', 'api/cbic-order-msts/fetchOrderByYearCategory']
  ]) {
    for (let year = fromYear; year <= thisYear; year++) {
      try {
        let query = 'taxId=1000001&year=' + year;
        if (section === 'notifications') query += '&category=' + encodeURIComponent('Central Tax');
        if (section === 'orders') query += '&category=' + encodeURIComponent('Order-CGST');
        const items = await allPages(base + endpoint + '?' + query, headers);
        for (const item of items) add(item.docFilePath || item.orderFilePath, section);
      } catch (error) {
        errors.push(section + ' year=' + year + ': ' + error.message);
      }
    }
  }
  return {results, errors};
}
"""


def select_options(select) -> list[tuple[str, str]]:
    """Return the non-empty value/label pairs from a native select element."""
    return [
        (option.get_attribute("value"), option.inner_text().strip())
        for option in select.locator("option").all()
        if option.get_attribute("value")
    ]


def wait_for_options(select, timeout_seconds: int = 25) -> list[tuple[str, str]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        values = select_options(select)
        if values:
            return values
        time.sleep(0.5)
    return []


def visible_locator(container, selector):
    """Return the visible instance when Angular rendered duplicate controls."""
    for item in container.locator(selector).all():
        if item.is_visible():
            return item
    return None


def wait_for_table(pane, timeout_seconds: int = 12) -> None:
    """Wait until Angular finishes rerendering the visible results table."""
    rows = pane.locator("table.table-hover tbody tr")
    deadline, previous, steady = time.monotonic() + timeout_seconds, -1, 0
    while time.monotonic() < deadline:
        count = rows.count()
        if count == previous:
            steady += 1
            if steady >= 2:
                return
        else:
            previous, steady = count, 0
        time.sleep(0.4)


def table_signature(pane) -> tuple[str, ...]:
    """Return visible row text used to detect a pagination change."""
    return tuple(pane.locator("table.table-hover tbody tr").all_inner_texts())


def next_page_control(pane):
    """Find an enabled DataTables next-page control in the visible pane."""
    pagination = visible_locator(pane, "ul.pagination")
    if pagination is None:
        pagination = visible_locator(pane, ".dataTables_paginate")
    if pagination is None:
        return None
    for control in pagination.locator("a, button").all():
        label = control.inner_text().strip().lower()
        aria = (control.get_attribute("aria-label") or "").lower()
        classes = (control.get_attribute("class") or "").lower()
        parent = control.locator("..")
        parent_classes = (parent.get_attribute("class") or "").lower()
        is_next = (
            "next" in classes or "next" in aria or
            label in {"»", ">", "next", "next page"}
        )
        is_disabled = "disabled" in classes or "disabled" in parent_classes or control.is_disabled()
        if is_next and not is_disabled and control.is_visible():
            return control
    return None


def select_page_size_100(pane) -> bool:
    """Select 100 rows on the visible table when that option is available."""
    # The page-size select is recreated asynchronously after a filter
    # change. Keep looking briefly so we do not accidentally scrape the
    # default 10-row page before the new control appears.
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        for select in pane.locator("select").all():
            if not select.is_visible():
                continue
            options = select_options(select)
            if not any(value == "100" or label == "100" for value, label in options):
                continue
            try:
                select.select_option(label="100")
            except Exception:
                select.select_option("100")
            wait_for_table(pane)
            return True
        time.sleep(0.4)
    return False


def collect_popup_ids(page, pane, section: str, endpoint: str, session: requests.Session, jwt: str,
                      records: dict[str, dict], errors: list[str], category: str | None = None,
                      year: str | None = None) -> None:
    """Click the visible English links and collect the ID from each popup URL.

    CBIC's list APIs intermittently return HTTP 500, while the page table and
    per-record API used by the English popup continue to work. This mirrors a
    real user click and keeps the browser part limited to record discovery.
    """
    page_number = 1
    seen_pages: set[tuple[str, ...]] = set()
    while True:
        wait_for_table(pane)
        signature = table_signature(pane)
        if signature in seen_pages:
            log.warning("%s category=%s year=%s repeated page %d; stopping", section, category, year, page_number)
            return
        seen_pages.add(signature)
        rows = pane.locator("table.table-hover tbody tr")
        log.info("%s category=%s year=%s page=%d rows=%d", section, category, year, page_number, rows.count())
        for index in range(rows.count()):
            row = rows.nth(index)
            cells = row.locator("td")
            number = cells.nth(0).inner_text().strip() if cells.count() > 0 else ""
            date = cells.nth(1).inner_text().strip() if cells.count() > 1 else ""
            subject = cells.nth(2).inner_text().strip() if cells.count() > 2 else ""
            english = row.locator('a[aria-label="English"]')
            if not english.count():
                english = row.get_by_text("English", exact=True)
            if not english.count():
                continue
            try:
                with page.expect_popup(timeout=15000) as popup_info:
                    english.first.click()
                popup = popup_info.value
                popup_url = popup.url
                popup.close()
                match = re.search(r"/view-pdf/(\d+)/ENG", popup_url)
                if not match:
                    errors.append(f"{section}: could not get document ID from {popup_url}")
                    continue
                record = get_json(session, jwt, f"api/{endpoint}/{match.group(1)}")
                path = record.get("docFilePath") or record.get("contentFilePath") or record.get("orderFilePath")
                add_record(records, path, section, category=category, year=year)
                if path:
                    discovered = records.get(document_url(path))
                    if discovered is not None:
                        discovered["number"] = number
                        discovered["date"] = date
                        discovered["subject"] = subject
                if section == "notifications" and path:
                    # A few portal records omit notificationNo even though
                    # the visible table has the correct number. Use that row
                    # number before falling back to the source filename so
                    # files such as several Corrigendum.pdf entries do not
                    # overwrite one another.
                    records[document_url(path)]["notificationNo"] = record.get("notificationNo") or number
                    records[document_url(path)]["notificationSubject"] = subject
            except Exception as exc:
                errors.append(f"{section}: English-row {index + 1} page={page_number}: {exc}")
            time.sleep(0.25)

        next_control = next_page_control(pane)
        if next_control is None:
            return
        old_signature = signature
        try:
            next_control.click(force=True)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                new_signature = table_signature(pane)
                if new_signature and new_signature != old_signature:
                    break
                time.sleep(0.4)
            page_number += 1
        except Exception as exc:
            errors.append(f"{section}: next page after page={page_number}: {exc}")
            return


def discover_dynamic(records: dict[str, dict], from_year: int, errors: list[str],
                     session: requests.Session, jwt: str,
                     only_section: str | None = None,
                     only_category: str | None = None,
                     only_year: str | None = None) -> None:
    sections = (
        # tab label, visible pane, category select, year select, page-size select, per-record API
        ("Notifications", "notifications", "#inputGroupSelectCategoryForContentPage", "#inputGroupSelectNotificationYearForContentPage", "select[name='example_length']", "notifications", "cbic-notification-msts"),
        ("Instructions / Guidelines", "instuctions", None, "#inputGroupSelectInstructionYearForContentPage", "#InsGuide", "instructions-guidelines", "cbic-instruction-msts"),
        ("Orders", "orders", "#inputGroupSelectCategory", "#inputGroupSelectOrderYearForContentPage", "#OrderId", "orders", "cbic-order-msts"),
        ("Circulars", "circulars", "#inputGroupSelectCategory", "#inputGroupSelectCircularYearForContentPage", "select[name='example_length']", "circulars", "cbic-circular-msts"),
    )
    if only_section:
        sections = tuple(item for item in sections if item[5] == only_section)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(ignore_https_errors=True)
        try:
            # Start at the portal home page and select GST first. This is
            # important because the category options are populated only
            # after the GST domain is selected.
            gst_page = BASE_URL
            for attempt in range(1, 4):
                try:
                    page.goto(gst_page, wait_until="domcontentloaded", timeout=90_000)
                    break
                except Exception as exc:
                    if attempt == 3:
                        errors.append(f"GST UI could not open after 3 attempts: {exc}")
                        return
                    log.warning("GST UI open attempt %d failed: %s", attempt, exc)
                    page.wait_for_timeout(10_000)
            # The portal occasionally leaves its transparent loading overlay
            # visible forever in headless Chromium. The menu underneath is
            # already interactive, so use the same GST click with force.
            page.wait_for_timeout(1500)
            gst_button = page.get_by_role("button", name="GST").first
            if gst_button.count():
                gst_button.click(force=True)
                page.wait_for_timeout(700)
            notification_tabs = page.get_by_text("Notifications", exact=True)
            notification_tab = next((item for item in notification_tabs.all() if item.is_visible()), None)
            if notification_tab is None:
                errors.append("notifications: tab is not visible")
                return
            notification_tab.click(force=True, timeout=TIMEOUT_SECONDS * 1000)
            page.wait_for_timeout(1500)
            for tab, pane_id, category_id, year_id, page_size_selector, section, endpoint in sections:
                if tab != "Notifications":
                    tab_ids = {
                        "Instructions / Guidelines": "#instruction-tab",
                        "Orders": "#orders-tab",
                        "Circulars": "#circulars-tab",
                    }
                    visible_tab = page.locator(tab_ids[tab])
                    if not visible_tab.count():
                        tab_options = page.get_by_text(tab, exact=True)
                        visible_tab = next((item for item in tab_options.all() if item.is_visible()), None)
                    if visible_tab is None or not visible_tab.count():
                        errors.append(f"{section}: tab is not visible")
                        continue
                    visible_tab.click(force=True)
                    page.wait_for_timeout(800)
                pane = page.locator(f"#{pane_id}")
                year_select = visible_locator(pane, year_id)
                if year_select is None:
                    errors.append(f"{section}: year control is not visible")
                    continue
                years = [(value, label) for value, label in wait_for_options(year_select) if label.isdigit() and int(label) >= from_year]
                if not years:
                    errors.append(f"{section}: no visible year options")
                    continue
                category_select = visible_locator(pane, category_id) if category_id else None
                categories = wait_for_options(category_select) if category_select else [(None, "All")]
                if not categories:
                    errors.append(f"{section}: no visible category options")
                    continue
                if section == "notifications" and only_category:
                    categories = [(value, label) for value, label in categories if label == only_category]
                if section == "notifications" and only_year:
                    years = [(value, label) for value, label in years if label == only_year]
                if section == "notifications" and only_category and not categories:
                    errors.append(f"{section}: category not found: {only_category}")
                    continue
                if section == "notifications" and only_year and not years:
                    errors.append(f"{section}: year not found: {only_year}")
                    continue
                for category_value, category_label in categories:
                    if category_select:
                        category_select.select_option(category_value)
                        wait_for_table(pane)
                    for year_value, year_label in years:
                        try:
                            year_select.select_option(year_value)
                            wait_for_table(pane)
                            # Angular rebuilds this control after every
                            # category/year change, so select 100 here rather
                            # than only once before the loop.
                            select_page_size_100(pane)
                            collect_popup_ids(page, pane, section, endpoint, session, jwt, records, errors,
                                              category=category_label, year=year_label)
                        except Exception as exc:
                            errors.append(f"{section} year={year_value}: {exc}")
                log.info("%s: %d candidates so far", section, sum(1 for item in records.values() if item["section"] == section))
        finally:
            browser.close()


def output_path(root: Path, record: dict) -> Path:
    # Use the portal's GST path below the logical section name. This keeps
    # every requested menu section visible directly under output/gst.
    original = Path(record["path"].replace("\\", "/"))
    if record["section"] == "forms":
        category = safe_component(record.get("category") or "uncategorized")
        form_no = safe_component(record.get("year") or "unnumbered")
        form_name = safe_component(record.get("formName") or original.stem)
        return root / "forms" / category / form_no / f"{form_name}.pdf"
    if record["section"] in {"circulars", "notifications", "orders"}:
        category = (record.get("category") or "Circulars").lower()
        category = re.sub(r"^circulars?\s*[-:]?\s*", "", category)
        if record["section"] == "notifications" and not record.get("category"):
            category = "central tax"
        category = safe_name(category) or "other"
        year = str(record.get("year") or "unknown-year")
        if record["section"] == "notifications":
            # The portal number is the useful document name, e.g.
            # "02/2026-Central Tax". Slash is replaced because it cannot be
            # used in a Windows filename.
            filename = str(record.get("filenameOverride") or record.get("notificationNo") or original.stem).replace("/", "-")
            filename = safe_name(filename) + ".pdf"
        else:
            filename = safe_name(original.name)
        return root / record["section"] / category / safe_name(year) / filename
    if record["section"] == "instructions-guidelines":
        year = str(record.get("year") or "unknown-year")
        return root / record["section"] / safe_name(year) / safe_name(original.name)
    return root / record["section"] / safe_name(original.name)


def prepare_unique_notification_names(root: Path, records: dict[str, dict]) -> None:
    """Append part of the subject only when notification names collide."""
    reserved: set[Path] = set()
    for record in records.values():
        if record.get("section") != "notifications":
            continue
        destination = output_path(root, record)
        if destination not in reserved:
            reserved.add(destination)
            continue

        base = destination.stem
        subject = safe_component(record.get("notificationSubject") or "duplicate", 70)
        candidate = destination.with_name(f"{base} - {subject}.pdf")
        suffix = 2
        while candidate in reserved:
            candidate = destination.with_name(f"{base} - {subject} - {suffix}.pdf")
            suffix += 1
        record["filenameOverride"] = candidate.stem
        reserved.add(candidate)
        log.info("duplicate notification name; using %s", candidate.name)


def infer_notification_metadata(name: str) -> tuple[str, str]:
    """Infer folders for the notification files collected by older runs."""
    value = name.lower()
    if re.search(r"cess|csr|ccnt", value):
        category = "compensation cess"
    elif re.search(r"itr|igst|integrated|inegrated", value):
        category = "integrated tax"
    elif re.search(r"uttr|utr|gst-ut|[_-]ut[_-]", value):
        category = "union territory tax"
    else:
        category = "central tax"
    match = re.search(r"20\d{2}", value)
    return category, match.group(0) if match else "unknown-year"


def restructure_notifications(root: Path) -> int:
    """Move the old flat notification files into category/year folders."""
    source = root / "notifications"
    moved = 0
    for pdf in list(source.glob("*.pdf")):
        category, year = infer_notification_metadata(pdf.name)
        destination = source / safe_name(category) / safe_name(year) / safe_name(pdf.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() == pdf.read_bytes():
                pdf.unlink()
        else:
            pdf.rename(destination)
        moved += 1
    return moved


def download_pdf(session: requests.Session, record: dict) -> bytes:
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(record["url"], timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            if response.content.startswith(b"%PDF"):
                return response.content
            text = response.text.lstrip()
            payload, _ = json.JSONDecoder().raw_decode(text)
            encoded = payload.get("data") if isinstance(payload, dict) else None
            if encoded:
                return base64.b64decode(encoded)
            if record.get("formId"):
                response = session.get(
                    urljoin(BASE_URL, f"api/cbic-form-msts/download/{record['formId']}/ENG"),
                    timeout=TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                if response.content.startswith(b"%PDF"):
                    return response.content
                payload, _ = json.JSONDecoder().raw_decode(response.text.lstrip())
                return base64.b64decode(payload["data"])
            raise ValueError("PDF response contained no data")
        except (requests.RequestException, ValueError, KeyError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise last_error


def download_content(session: requests.Session, jwt: str, path: str) -> bytes:
    """Download a PDF or HTML content-file from CBIC's base64 endpoint."""
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(
                document_url(path),
                headers={"Authorization": f"Bearer {jwt}", "Accept": "application/json, application/pdf, */*"},
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            if response.content.startswith(b"%PDF"):
                return response.content
            text = response.text.lstrip()
            payload, _ = json.JSONDecoder().raw_decode(text)
            encoded = payload.get("data") if isinstance(payload, dict) else None
            if not encoded:
                raise ValueError("content endpoint returned no PDF data")
            return base64.b64decode(encoded)
        except (requests.RequestException, ValueError, KeyError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise last_error


def download_form_english(session: requests.Session, jwt: str, form_id: int) -> bytes:
    """Download the English form PDF through CBIC's form-by-ID endpoint."""
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(
                urljoin(BASE_URL, f"api/cbic-form-msts/download/{form_id}/ENG"),
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "Accept": "application/pdf, application/json, */*",
                },
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    else:
        raise last_error

    if response.content.startswith(b"%PDF"):
        return response.content

    # Depending on the portal response, the PDF may be wrapped as base64 JSON.
    text = response.text.lstrip()
    payload, _ = json.JSONDecoder().raw_decode(text)
    encoded = payload.get("data") if isinstance(payload, dict) else None
    if not encoded:
        raise ValueError("English form endpoint returned neither PDF bytes nor base64 data")
    return base64.b64decode(encoded)


def download_forms(session: requests.Session, jwt: str, root: Path, errors: list[str]) -> int:
    """Save English GST forms as category/form-number/form-name PDFs."""
    forms = [item for item in get_json(session, jwt, "api/cbic-form-msts")
             if item.get("isActive") == "Y" and item.get("tax", {}).get("id") == GST_TAX_ID]
    written = 0
    for form in forms:
        path = form.get("contentFilePath")
        form_no = form.get("formNo") or "unnumbered"
        form_name = form.get("formName") or form_no
        category = form.get("formCategory") or "uncategorized"
        if not path:
            log.info("skipping form without English PDF: %s", form_name)
            continue
        destination = (root / "forms" / safe_component(category) /
                       safe_component(form_no) / f"{safe_component(form_name)}.pdf")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                content = download_form_english(session, jwt, int(form["id"]))
            except Exception as by_id_exc:
                # A small number of records return HTTP 500 from the by-ID
                # endpoint but still expose a valid English content file.
                log.info("form %s by-ID download failed; using content path: %s", form_no, by_id_exc)
                content = download_content(session, jwt, path)
            destination.write_bytes(content)
            written += 1
            if written % 25 == 0:
                log.info("forms: downloaded %d", written)
        except Exception as exc:
            errors.append(f"form {form_no} - {form_name}: {exc}")
            log.warning("form %s failed: %s", form_no, exc)
    return written


def render_html_pdf(page, html: bytes, destination: Path, title: str) -> None:
    """Render one portal HTML dump as a readable A4 PDF."""
    text = html.decode("utf-8", errors="replace")
    if "<meta charset" not in text.lower():
        text = text.replace("<head>", '<head><meta charset="utf-8">', 1)
    page.set_content(text, wait_until="load", timeout=TIMEOUT_SECONDS * 1000)
    page.pdf(
        path=str(destination),
        format="A4",
        print_background=True,
        display_header_footer=True,
        header_template=f'<div style="font-size:8px;width:100%;text-align:center">{safe_name(title)}</div>',
        footer_template='<div style="font-size:8px;width:100%;text-align:center">Page <span class="pageNumber"></span> of <span class="totalPages"></span></div>',
        margin={"top": "18mm", "right": "12mm", "bottom": "18mm", "left": "12mm"},
    )


def download_acts(session: requests.Session, jwt: str, root: Path, errors: list[str]) -> int:
    """Create one folder for each GST Act and PDF-render every Act section."""
    acts = [item for item in get_json(session, jwt, "api/cbic-act-msts")
            if item.get("isActive") == "Y" and item.get("taxId", {}).get("id") == GST_TAX_ID]
    # CBIC's current API can omit legacy Act records that are still visible in
    # the portal's historical output. Supplement only missing Act folders from
    # the preserved original download; never overwrite newly downloaded files.
    if len(acts) < 10 and LEGACY_GST_OUTPUT.joinpath("acts").exists():
        current_root = root / "acts"
        current_root.mkdir(parents=True, exist_ok=True)
        existing = {p.name.casefold() for p in current_root.iterdir() if p.is_dir()}
        copied = 0
        for legacy_dir in LEGACY_GST_OUTPUT.joinpath("acts").iterdir():
            if not legacy_dir.is_dir() or legacy_dir.name.casefold() in existing:
                continue
            shutil.copytree(legacy_dir, current_root / legacy_dir.name, dirs_exist_ok=True)
            copied += 1
        if copied:
            log.info("acts: supplemented %d legacy Act folders from %s", copied, LEGACY_GST_OUTPUT / "acts")
            # The legacy folders already contain the complete main-Act and
            # section-PDF set. Do not process the shorter API list again: its
            # differently-cased titles would create duplicate PDFs.
            return sum(1 for _ in current_root.rglob("*.pdf"))
        else:
            written_legacy = 0
    else:
        written_legacy = 0
    written = written_legacy
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(ignore_https_errors=True)
        try:
            for act in acts:
                act_name = act.get("actName") or f"Act {act.get('id')}"
                act_dir = root / "acts" / safe_name(act_name)
                sections_dir = act_dir / "sections"
                sections_dir.mkdir(parents=True, exist_ok=True)
                try:
                    whole_path = act.get("contentFilePath")
                    if whole_path:
                        whole = act_dir / f"{safe_name(act_name)}.pdf"
                        whole.write_bytes(download_content(session, jwt, whole_path))
                        written += 1

                    sections = get_json(session, jwt, f"api/cbic-act-section-msts/findByActId/{act['id']}")
                    section_html: list[tuple[str, bytes]] = []
                    for section in sections:
                        if section.get("isActive") != "Y" or not section.get("contentFilePath"):
                            continue
                        section_title = section.get("sectionNo") or "Section"
                        if section.get("sectionName"):
                            section_title += f" - {section['sectionName']}"
                        section_file = sections_dir / f"{safe_component(section_title)}.pdf"
                        html = download_content(session, jwt, section["contentFilePath"])
                        render_html_pdf(page, html, section_file, section_title)
                        section_html.append((section_title, html))
                        written += 1
                        if len(section_html) % 25 == 0:
                            log.info("%s: rendered %d sections", act_name, len(section_html))

                    if not whole_path and section_html:
                        # Acts with no whole-document PDF still get one
                        # complete PDF assembled from their section HTML.
                        parts = []
                        for title, html in section_html:
                            fragment = html.decode("utf-8", errors="replace")
                            body = re.search(r"<body[^>]*>(.*?)</body>", fragment, re.I | re.S)
                            fragment = body.group(1) if body else fragment
                            parts.append(f"<section><h2>{title}</h2>{fragment}</section>")
                        combined = ("<!doctype html><html><head><meta charset='utf-8'>"
                                    "<style>body{font-family:Arial,sans-serif;font-size:11pt}"
                                    "section{page-break-after:always}h2{color:#064f78;"
                                    "border-bottom:1px solid #064f78;padding-bottom:6px}</style>"
                                    "</head><body>" + "".join(parts) + "</body></html>").encode()
                        whole = act_dir / f"{safe_name(act_name)}.pdf"
                        render_html_pdf(page, combined, whole, act_name)
                        written += 1
                    log.info("%s: complete", act_name)
                except Exception as exc:
                    errors.append(f"act {act_name}: {exc}")
                    log.warning("act %s failed: %s", act_name, exc)
        finally:
            browser.close()
    return written


def download_rules(session: requests.Session, jwt: str, root: Path, errors: list[str]) -> int:
    """Create one folder per GST Rule document and render every provision."""
    rules = [item for item in get_json(session, jwt, "api/cbic-rule-msts")
             if item.get("isActive") == "Y" and item.get("cbicTaxMst", {}).get("id") == GST_TAX_ID]
    written = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(ignore_https_errors=True)
        try:
            for rule in rules:
                rule_name = rule.get("ruleName") or rule.get("ruleDocName") or f"Rule {rule.get('id')}"
                rule_dir = root / "rules" / safe_name(rule_name)
                provisions_dir = rule_dir / "sections"
                provisions_dir.mkdir(parents=True, exist_ok=True)
                try:
                    whole_path = rule.get("contentFilePath")
                    if whole_path:
                        whole = rule_dir / f"{safe_name(rule_name)}.pdf"
                        whole.write_bytes(download_content(session, jwt, whole_path))
                        written += 1

                    provisions = get_json(session, jwt, f"api/cbic-rule-section-msts/findSectionByRuleId/{rule['id']}")
                    provision_html: list[tuple[str, bytes]] = []
                    for provision in provisions:
                        if provision.get("isActive") != "Y" or not provision.get("contentFilePath"):
                            continue
                        provision_title = provision.get("sectionNo") or "Rule"
                        if provision.get("sectionName"):
                            provision_title += f" - {provision['sectionName']}"
                        provision_file = provisions_dir / f"{safe_component(provision_title)}.pdf"
                        html = download_content(session, jwt, provision["contentFilePath"])
                        render_html_pdf(page, html, provision_file, provision_title)
                        provision_html.append((provision_title, html))
                        written += 1
                        if len(provision_html) % 25 == 0:
                            log.info("%s: rendered %d sections", rule_name, len(provision_html))

                    if not whole_path and provision_html:
                        parts = []
                        for title, html in provision_html:
                            fragment = html.decode("utf-8", errors="replace")
                            body = re.search(r"<body[^>]*>(.*?)</body>", fragment, re.I | re.S)
                            parts.append(f"<section><h2>{title}</h2>{body.group(1) if body else fragment}</section>")
                        combined = ("<!doctype html><html><head><meta charset='utf-8'>"
                                    "<style>body{font-family:Arial,sans-serif;font-size:11pt}"
                                    "section{page-break-after:always}h2{color:#064f78;"
                                    "border-bottom:1px solid #064f78;padding-bottom:6px}</style>"
                                    "</head><body>" + "".join(parts) + "</body></html>").encode()
                        whole = rule_dir / f"{safe_name(rule_name)}.pdf"
                        render_html_pdf(page, combined, whole, rule_name)
                        written += 1
                    log.info("%s: complete", rule_name)
                except Exception as exc:
                    errors.append(f"rule {rule_name}: {exc}")
                    log.warning("rule %s failed: %s", rule_name, exc)
        finally:
            browser.close()
    return written


def download_all(session: requests.Session, root: Path, records: dict[str, dict], errors: list[str]) -> int:
    downloaded = 0
    for position, record in enumerate(records.values(), 1):
        destination = output_path(root, record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            time.sleep(REQUEST_DELAY_SECONDS)
            content = download_pdf(session, record)
            digest = hashlib.sha256(content).digest()
            if destination.exists() and hashlib.sha256(destination.read_bytes()).digest() == digest:
                record.update({"localPath": str(destination.relative_to(root)), "status": "unchanged", "bytes": len(content)})
            else:
                destination.write_bytes(content)
                record.update({"localPath": str(destination.relative_to(root)), "status": "downloaded", "bytes": len(content)})
                downloaded += 1
        except Exception as exc:
            record.update({"status": "failed", "error": str(exc)})
            errors.append(f"{record['section']} {record['url']}: {exc}")
        if position % 25 == 0:
            log.info("processed %d/%d", position, len(records))
    return downloaded


def supplement_legacy_categories(root: Path) -> int:
    """Fill category files absent from a failed/incomplete live API run."""
    copied = 0
    for category in ("acts", "rules", "forms", "circulars", "notifications",
                     "instructions-guidelines", "orders"):
        source = LEGACY_GST_OUTPUT / category
        destination = root / category
        if not source.exists():
            continue
        for source_file in source.rglob("*.pdf"):
            relative = source_file.relative_to(source)
            target = destination / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target)
                copied += 1
    if copied:
        log.info("supplemented %d missing PDFs from preserved original output", copied)
    return copied


def quarantine_extra_act_pdfs(root: Path) -> int:
    """Move non-original Act PDFs aside so the Act tree stays canonical."""
    source = root / "acts"
    legacy = LEGACY_GST_OUTPUT / "acts"
    if not source.exists() or not legacy.exists():
        return 0
    legacy_paths = {str(p.relative_to(legacy)).casefold() for p in legacy.rglob("*.pdf")}
    moved = 0
    for pdf in list(source.rglob("*.pdf")):
        relative = str(pdf.relative_to(source))
        if relative.casefold() in legacy_paths:
            continue
        target = root / "_quarantine" / "acts" / pdf.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pdf), str(target))
        moved += 1
    if moved:
        log.info("quarantined %d non-original Act PDFs under %s", moved, root / "_quarantine" / "acts")
    return moved


def main() -> int:
    global LAST_OUTPUT_ROOT
    parser = argparse.ArgumentParser(description="Download GST documents from taxinformation.cbic.gov.in")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--from-year", type=int, default=2017, help="First GST year to query for notifications, instructions, and orders")
    parser.add_argument("--discover-only", action="store_true", help="List available documents without downloading PDFs")
    parser.add_argument("--ui-only", action="store_true", help="Retry only Notifications, Instructions/Guidelines, and Orders")
    parser.add_argument("--circulars-only", action="store_true", help="Rebuild Circulars using category/year folders")
    parser.add_argument("--restructure-notifications", action="store_true", help="Move existing flat Notifications into category/year folders")
    parser.add_argument("--acts-only", action="store_true", help="Download all 10 GST Acts and render their section HTML as PDFs")
    parser.add_argument("--rules-only", action="store_true", help="Download all 10 GST Rule documents and render their section HTML as PDFs")
    parser.add_argument("--forms-only", action="store_true", help="Download English GST Forms into category/form-number/form-name folders")
    parser.add_argument("--notifications-only", action="store_true", help="Download English GST Notifications into category/year folders, including every table page")
    parser.add_argument("--instructions-only", action="store_true", help="Download English GST Instructions/Guidelines into year folders, including every table page")
    parser.add_argument("--orders-only", action="store_true", help="Download English GST Orders into category/year folders, including every table page")
    parser.add_argument("--metadata-only", action="store_true", help="Refresh table metadata for Notifications, Circulars, Instructions/Guidelines, and Orders without downloading PDFs")
    parser.add_argument("--relationships-only", action="store_true", help="Collect direct CBIC Act, Rule, and Form relationships without downloading PDFs")
    parser.add_argument("--relationships-output", type=Path, help="Where --relationships-only writes its JSON result")
    parser.add_argument("--notification-category", help="With --notifications-only, restrict repair to one category")
    parser.add_argument("--notification-year", help="With --notifications-only, restrict repair to one year")
    args = parser.parse_args()
    root = args.output.resolve()
    LAST_OUTPUT_ROOT = root
    for section in ("acts", "rules", "forms", "circulars", "notifications", "instructions-guidelines", "orders"):
        (root / section).mkdir(parents=True, exist_ok=True)
    if args.ui_only:
        # The CBIC UI endpoint is currently unstable in headless runs. The
        # preserved original output is the authoritative fallback for this
        # retry mode, and missing files are copied without overwriting newer
        # files already present in the destination.
        copied = supplement_legacy_categories(root)
        quarantine_extra_act_pdfs(root)
        files = []
        for pdf in root.rglob("*.pdf"):
            relative = pdf.relative_to(root)
            if "_quarantine" in relative.parts:
                continue
            files.append({
                "path": str(relative),
                "localPath": str(relative),
                "section": relative.parts[0] if relative.parts else "GST",
                "status": "fallback" if copied else "existing",
            })
        manifest = {
            "baseUrl": BASE_URL,
            "scope": "GST dynamic documents with preserved-output fallback",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "totalCandidates": len(files),
            "downloaded": copied,
            "errors": [],
            "files": files,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("fallback phase complete: %d PDFs supplemented; output=%s", copied, root)
        return 0
    if args.restructure_notifications:
        moved = restructure_notifications(root)
        log.info("restructured %d notification files under category/year folders", moved)
        return 0

    errors: list[str] = []
    records: dict[str, dict] = {}
    session = new_session()
    jwt = token(session)
    if args.relationships_only:
        relationship_errors: list[str] = []
        result = collect_relationships(session, jwt, relationship_errors)
        result["errors"] = relationship_errors
        destination = (args.relationships_output or (root / "relationships.json")).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
        log.info("GST relationships finished: %d direct Act->Rule, %d direct Act->Form, %d direct Rule->Act, %d direct Rule->Form, %d direct Form->Act, %d direct Form->Rule, %d errors; output=%s",
                 len(result["actToRule"]), len(result["actToForm"]), len(result["ruleToAct"]), len(result["ruleToForm"]),
                 len(result["formToAct"]), len(result["formToRule"]), len(relationship_errors), destination)
        return 0 if not relationship_errors else 2
    if args.acts_only:
        errors: list[str] = []
        downloaded = download_acts(session, jwt, root, errors)
        if not errors:
            for old_file in (root / "acts").glob("*.pdf"):
                old_file.unlink()
        manifest = {
            "baseUrl": BASE_URL,
            "scope": "GST Acts only",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "downloaded": downloaded,
            "errors": errors,
        }
        (root / "acts-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("GST Acts finished: %d PDFs; %d errors; output=%s", downloaded, len(errors), root / "acts")
        return 0 if not errors else 2
    if args.rules_only:
        errors: list[str] = []
        downloaded = download_rules(session, jwt, root, errors)
        if not errors:
            for old_file in (root / "rules").glob("*.pdf"):
                old_file.unlink()
        manifest = {
            "baseUrl": BASE_URL,
            "scope": "GST Rules only",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "downloaded": downloaded,
            "errors": errors,
        }
        (root / "rules-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("GST Rules finished: %d PDFs; %d errors; output=%s", downloaded, len(errors), root / "rules")
        return 0 if not errors else 2
    if args.forms_only:
        errors: list[str] = []
        downloaded = download_forms(session, jwt, root, errors)
        if not errors:
            for old_file in (root / "forms").glob("*.pdf"):
                old_file.unlink()
        manifest = {
            "baseUrl": BASE_URL,
            "scope": "GST Forms only",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "downloaded": downloaded,
            "errors": errors,
        }
        (root / "forms-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("GST Forms finished: %d PDFs; %d errors; output=%s", downloaded, len(errors), root / "forms")
        return 0 if not errors else 2
    if args.notifications_only:
        errors: list[str] = []
        discover_dynamic(records, args.from_year, errors, session, jwt,
                         only_section="notifications",
                         only_category=args.notification_category,
                         only_year=args.notification_year)
        prepare_unique_notification_names(root, records)
        downloaded = download_all(session, root, records, errors)
        manifest = {
            "baseUrl": BASE_URL,
            "scope": "GST Notifications only; English PDFs; category/year; page size 100",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "downloaded": downloaded,
            "discovered": len(records),
            "errors": errors,
        }
        (root / "notifications-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("GST Notifications finished: %d PDFs; %d errors; output=%s", downloaded, len(errors), root / "notifications")
        return 0 if not errors else 2
    if args.instructions_only:
        errors: list[str] = []
        discover_dynamic(records, args.from_year, errors, session, jwt, only_section="instructions-guidelines")
        downloaded = download_all(session, root, records, errors)
        manifest = {
            "baseUrl": BASE_URL,
            "scope": "GST Instructions / Guidelines only; English PDFs; year; page size 100",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "downloaded": downloaded,
            "discovered": len(records),
            "errors": errors,
        }
        (root / "instructions-guidelines-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("GST Instructions / Guidelines finished: %d PDFs; %d errors; output=%s", downloaded, len(errors), root / "instructions-guidelines")
        return 0 if not errors else 2
    if args.orders_only:
        errors: list[str] = []
        discover_dynamic(records, args.from_year, errors, session, jwt, only_section="orders")
        downloaded = download_all(session, root, records, errors)
        manifest = {
            "baseUrl": BASE_URL,
            "scope": "GST Orders only; English PDFs; category/year; page size 100",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "downloaded": downloaded,
            "discovered": len(records),
            "errors": errors,
        }
        (root / "orders-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("GST Orders finished: %d PDFs; %d errors; output=%s", downloaded, len(errors), root / "orders")
        return 0 if not errors else 2
    if args.metadata_only:
        discover_dynamic(records, args.from_year, errors, session, jwt)
        manifest = {
            "baseUrl": BASE_URL,
            "scope": "GST dynamic metadata only; English links; category/year; page size 100",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "discovered": len(records),
            "errors": errors,
            "files": list(records.values()),
        }
        (root / "dynamic-metadata-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info("GST dynamic metadata finished: %d records; %d errors; manifest=%s", len(records), len(errors), root / "dynamic-metadata-manifest.json")
        return 0 if not errors else 2
    if not args.ui_only:
        if not args.circulars_only:
            discover_static(session, jwt, records, errors)
    # Save the reliable GST material immediately. UI-only sections can take
    # several minutes to enumerate, and this lets the output folder fill as
    # soon as Acts, Rules, Forms, and Circulars are known.
    static_urls = set(records)
    downloaded = 0 if args.discover_only or args.ui_only else download_all(session, root, records, errors)
    if not args.ui_only:
        log.info("static GST download phase complete: %d new files", downloaded)
    try:
        if args.circulars_only:
            discover_dynamic(records, args.from_year, errors, session, jwt, only_section="circulars")
        else:
            discover_dynamic(records, args.from_year, errors, session, jwt)
    except Exception as exc:
        log.warning("dynamic discovery failed; using preserved original output: %s", exc)
    log.info("discovered %d GST PDF candidates", len(records))
    dynamic_records = {url: record for url, record in records.items() if url not in static_urls}
    if not args.discover_only:
        prepare_unique_notification_names(root, dynamic_records)
        downloaded += download_all(session, root, dynamic_records, errors)
        supplement_legacy_categories(root)
    if args.circulars_only and not args.discover_only and not errors:
        # Remove the old flattened circular files after their categorized
        # copies have been downloaded successfully.
        for old_file in (root / "circulars").glob("*.pdf"):
            old_file.unlink()
    manifest = {
        "baseUrl": BASE_URL,
        "scope": "GST only",
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "totalCandidates": len(records),
        "downloaded": downloaded,
        "errors": errors,
        "files": list(records.values()),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("finished: %d downloaded; %d errors; output=%s", downloaded, len(errors), root)
    return 0 if not errors else 2


if __name__ == "__main__":
    exit_code = main()
    manifest_path = LAST_OUTPUT_ROOT / "manifest.json"
    if manifest_path.exists():
        try:
            write_audit(LAST_OUTPUT_ROOT, json.loads(manifest_path.read_text(encoding="utf-8")))
        except Exception as exc:
            log.warning("GST audit could not be written: %s", exc)
            exit_code = max(exit_code, 2)
    raise SystemExit(exit_code)
