"""Download Customs notifications and circulars from the CBIC Tax Information Portal.

Run from the scraper folder with:
    uv run python "005 Customs Notifications and Circulars.py"

English PDFs are stored under downloads/005 Customs Notifications and Circulars/
in category/year folders for notifications and year folders for circulars.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3
from playwright.sync_api import sync_playwright


BASE_URL = "https://taxinformation.cbic.gov.in/"
CUSTOMS_TAX_ID = 1000002
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = next(parent / ".env" for parent in Path(__file__).resolve().parents if (parent / "docker-compose.yml").is_file())
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip().strip('"')
DOWNLOAD_BASE = Path(os.environ.get("DOWNLOAD_BASE_PATH", SCRIPT_DIR))
DEFAULT_OUTPUT = DOWNLOAD_BASE / Path(__file__).stem
TIMEOUT_SECONDS = 60
REQUEST_DELAY = float(os.environ.get("SCRAPER_REQUEST_DELAY_SECONDS", "1.0"))
MAX_ATTEMPTS = int(os.environ.get("SCRAPER_MAX_ATTEMPTS", "3"))
LOG = logging.getLogger("customs-cbic")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def safe_name(value: str, limit: int = 150) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" ._")
    value = re.sub(r"\s+", " ", value)
    return (value or "unnamed")[:limit].rstrip(" .")


def request_retry(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        time.sleep(REQUEST_DELAY + random.uniform(0, 0.25))
        try:
            response = session.request(method, url, **kwargs)
            if response.status_code in (403, 429):
                raise RuntimeError(f"CBIC access throttled: HTTP {response.status_code} ({url})")
            if response.status_code < 500 or attempt == MAX_ATTEMPTS - 1:
                response.raise_for_status()
                return response
            last_error = RuntimeError(f"HTTP {response.status_code}: {url}")
        except Exception as exc:
            last_error = exc
            if "throttled" in str(exc):
                raise
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(min(30, 2 ** (attempt + 1)) + random.uniform(0, 1))
    raise last_error or RuntimeError(f"Request failed: {url}")


def get_json(session: requests.Session, url: str):
    response = request_retry(session, "GET", url, timeout=TIMEOUT_SECONDS)
    value, _ = json.JSONDecoder().raw_decode(response.text.lstrip())
    return value


def new_session() -> tuple[requests.Session, str]:
    session = requests.Session()
    session.verify = False  # CBIC's current NIC certificate chain is non-standard.
    session.headers.update({"Accept": "application/json", "User-Agent": "Customs-CBIC-document-collector/1.0"})
    response = request_retry(session, "POST", urljoin(BASE_URL, "api/authenticate-token"), timeout=TIMEOUT_SECONDS)
    return session, response.json()["id_token"]


def document_url(path: str) -> str:
    path = path.replace("\\", "/").lstrip("/")
    if not path.lower().startswith("tax_repository/customs/") or not path.lower().endswith(".pdf"):
        raise ValueError(f"Unexpected Customs PDF path: {path}")
    return urljoin(BASE_URL, "content/pdf/" + path)


def add_record(records: dict[str, dict], path: str | None, section: str, **metadata) -> None:
    if not path:
        return
    try:
        url = document_url(path)
    except ValueError:
        return
    record = records.setdefault(url, {"url": url, "path": path, "section": section})
    record.update({key: value for key, value in metadata.items() if value not in (None, "")})


def open_customs_listing(page, menu_item: str, tab_id: str) -> None:
    """Open the Customs menu item and its matching explore-page tab."""
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=90_000)
    customs_link = page.locator("a#navGST").filter(has_text="Customs").first
    customs_dropdown = customs_link.locator("xpath=..").locator("ul.dropdown-menu")
    # Angular preserves dropdown state across route changes; the first toggle
    # can close a menu left expanded by the previous listing. Ensure it is open.
    for _ in range(2):
        if customs_dropdown.is_visible():
            break
        customs_link.click(force=True, timeout=TIMEOUT_SECONDS * 1000)
        page.wait_for_timeout(250)
    if not customs_dropdown.is_visible():
        raise RuntimeError("Customs navigation dropdown did not become visible")
    # Menu anchors have padded accessible names and all use href="/". Scope
    # the text match to Customs' own dropdown rather than a global role lookup.
    customs_dropdown.locator("a.sm-hide").filter(has_text=menu_item).click(
        force=True, timeout=TIMEOUT_SECONDS * 1000
    )
    page.wait_for_timeout(700)
    page.locator(tab_id).click(force=True)


def discover_circulars(page, session: requests.Session, records: dict[str, dict], year: str,
                       output_root: Path | None = None, downloaded_total: list[int] | None = None) -> list[str]:
    """Use Customs > Circulars and collect English rows for the requested year."""
    errors: list[str] = []
    open_customs_listing(page, "Circulars", "#circulars-tab")
    pane = page.locator("#circulars")
    year_select = pane.locator("#inputGroupSelectCircularYearForContentPage")
    matches = [(option.get_attribute("value"), option.inner_text().strip())
               for option in year_select.locator("option").all()
               if option.get_attribute("value") and option.inner_text().strip() == year]
    if not matches:
        return [f"Customs circular year not found: {year}"]
    year_select.select_option(matches[0][0])
    page.wait_for_timeout(700)
    size_selector = "#circularTable"
    show_all_rows(page, pane, size_selector, f"Customs circulars {year}")

    seen: set[tuple[str, ...]] = set()
    page_no = 1
    while True:
        signature = table_signature(pane)
        if not signature or signature in seen:
            break
        info = table_page_info(pane, size_selector)
        if not info:
            errors.append(f"Circulars {year}: item-count label could not be read")
            break
        total_pages = max(1, (info[2] + info[3] - 1) // info[3])
        if page_no == 1:
            LOG.info("Customs Circulars %s: %d items across %d page(s)", year, info[2], total_pages)
        seen.add(signature)
        urls_before_page = set(records)
        rows = visible_rows(pane)
        for index in range(rows.count()):
            row = rows.nth(index)
            cells = row.locator("td")
            number = cells.nth(0).inner_text().strip() if cells.count() > 0 else ""
            date = cells.nth(1).inner_text().strip() if cells.count() > 1 else ""
            subject = cells.nth(2).inner_text().strip() if cells.count() > 2 else ""
            row_text = row.inner_text().strip().lower()
            if not (number or date or subject) or any(phrase in row_text for phrase in ("no data", "no matching", "no records")):
                continue
            english = row.locator('a[aria-label="English"]')
            if not english.count():
                english = row.get_by_text(re.compile(r"English", re.I))
            if not english.count():
                errors.append(f"Circulars {year}: English link missing for {number}")
                continue
            try:
                document_id = popup_document_id(page, english.first)
                record = get_json(session, urljoin(BASE_URL, f"api/cbic-circular-msts/{document_id}"))
                add_record(records, record.get("docFilePath"), "circulars", year=year,
                           number=record.get("circularNo") or number, date=date, subject=subject)
            except Exception as exc:
                errors.append(f"Circulars {year} row {index + 1}: {exc}")
            time.sleep(0.2)
        if output_root is not None and downloaded_total is not None:
            stage_records = {url: record for url, record in records.items() if url not in urls_before_page}
            if stage_records:
                count = download_all(session, stage_records, output_root, errors)
                downloaded_total[0] += count
                LOG.info("Customs Circulars %s page %d: downloaded %d PDFs", year, page_no, count)
        if page_no >= total_pages:
            break
        if not advance_table_page(page, pane, info, size_selector):
            errors.append(f"Circulars {year}: Next was unavailable at page {page_no} of {total_pages}")
            break
        page_no += 1
        if page_no > 100:
            errors.append(f"Circulars {year}: pagination limit reached")
            break
    LOG.info("Customs circulars discovered: %d", sum(r["section"] == "circulars" for r in records.values()))
    return errors


def visible_rows(pane):
    return pane.locator("table tbody tr")


def table_signature(pane) -> tuple[str, ...]:
    return tuple(visible_rows(pane).all_text_contents())


def table_page_info(pane, size_selector: str) -> tuple[int, int, int, int] | None:
    """Read current range, total items, and page size from the portal table."""
    labels = pane.locator("div[cbictranslate='global.item-count']")
    label_text = labels.first.inner_text() if labels.count() else pane.inner_text()
    match = re.search(r"Showing\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)\s+items", label_text)
    if not match:
        return None
    start, end, total = (int(part) for part in match.groups())
    size_control = pane.locator(size_selector)
    page_size = int(size_control.input_value()) if size_control.count() else max(1, end - start + 1)
    return start, end, total, page_size


def show_all_rows(page, pane, size_selector: str, description: str) -> None:
    """Select 100 rows and wait for the portal's item-count label to catch up."""
    size_select = pane.locator(size_selector)
    if not size_select.count():
        return
    size_select.select_option("100")
    deadline = time.monotonic() + 20
    empty_polls = 0
    while time.monotonic() < deadline:
        info = table_page_info(pane, size_selector)
        if info:
            start, end, total, page_size = info
            expected_end = min(total, page_size)
            if total == 0 or (start == 1 and end == expected_end
                              and visible_rows(pane).count() >= expected_end):
                return
        elif visible_rows(pane).count() == 0:
            empty_polls += 1
            if empty_polls >= 8:
                return
        else:
            empty_polls = 0
        page.wait_for_timeout(250)
    raise RuntimeError(f"{description}: item count did not settle")


def advance_table_page(page, pane, old_info: tuple[int, int, int, int], size_selector: str) -> bool:
    """Click the portal's li.page-item Next link and wait for its count to advance."""
    links = pane.locator("li.page-item > a[aria-label='Next']")
    if not links.count():
        return False
    link = links.first
    if "disabled" in ((link.locator("xpath=..").get_attribute("class") or "").lower()):
        return False
    link.click(force=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        new_info = table_page_info(pane, size_selector)
        if new_info and new_info != old_info:
            return True
        page.wait_for_timeout(250)
    raise RuntimeError("Next-page control was clicked but the item-count label did not change")


def popup_document_id(page, english_link) -> str:
    """Open an English link and recover its PDF ID, retrying one transient miss."""
    last_error = None
    for _ in range(2):
        try:
            with page.expect_popup(timeout=20_000) as popup_info:
                english_link.click(force=True)
            popup = popup_info.value
            popup_url = popup.url
            popup.close()
            match = re.search(r"/view-pdf/(\d+)/ENG", popup_url)
            if not match:
                raise RuntimeError(f"Could not read document id from {popup_url}")
            return match.group(1)
        except Exception as exc:
            last_error = exc
            page.wait_for_timeout(300)
    raise RuntimeError(f"English PDF popup failed after retry: {last_error}")


def discover_notifications(page, session: requests.Session, jwt: str, records: dict[str, dict], from_year: int,
                           only_year: str | None = None, only_category: str | None = None,
                           output_root: Path | None = None,
                           downloaded_total: list[int] | None = None) -> list[str]:
    errors: list[str] = []
    # Follow the exact Customs > Notifications menu path, then use the listing filters.
    open_customs_listing(page, "Notifications", "#notifications-tab")
    pane = page.locator("#notifications")
    category_select = pane.locator("#inputGroupSelectCategoryForContentPage")
    year_select = pane.locator("#inputGroupSelectNotificationYearForContentPage")
    category_options = category_select.locator("option").all()
    categories = [(option.get_attribute("value"), option.inner_text().strip()) for option in category_options
                  if option.get_attribute("value") and option.inner_text().strip()]
    year_options = year_select.locator("option").all()
    years = [(option.get_attribute("value"), option.inner_text().strip()) for option in year_options
             if option.get_attribute("value") and option.inner_text().strip().isdigit()
             and int(option.inner_text().strip()) >= from_year]
    if only_category:
        categories = [(value, label) for value, label in categories if label.casefold() == only_category.casefold()]
    if only_year:
        years = [(value, label) for value, label in years if label == only_year]
    if not categories:
        return [f"Customs notification category not found: {only_category}"] if only_category else ["No Customs notification categories found"]
    if not years:
        return [f"Customs notification year not found: {only_year}"] if only_year else [f"No Customs notification years found from {from_year}"]

    for category_value, category_label in categories:
        for year_value, year_label in years:
            try:
                category_select.select_option(category_value)
                year_select.select_option(year_value)
                page.wait_for_timeout(700)
                size_selector = "select[name='example_length']"
                show_all_rows(page, pane, size_selector,
                              f"Customs notifications {category_label} {year_label}")
                seen: set[tuple[str, ...]] = set()
                page_no = 1
                while True:
                    signature = table_signature(pane)
                    if not signature or signature in seen:
                        break
                    info = table_page_info(pane, size_selector)
                    if not info:
                        errors.append(f"{category_label} {year_label}: item-count label could not be read")
                        break
                    total_pages = max(1, (info[2] + info[3] - 1) // info[3])
                    if page_no == 1:
                        LOG.info("Customs Notifications %s %s: %d items across %d page(s)",
                                 category_label, year_label, info[2], total_pages)
                    seen.add(signature)
                    urls_before_page = set(records)
                    rows = visible_rows(pane)
                    for index in range(rows.count()):
                        row = rows.nth(index)
                        cells = row.locator("td")
                        number = cells.nth(0).inner_text().strip() if cells.count() > 0 else ""
                        date = cells.nth(1).inner_text().strip() if cells.count() > 1 else ""
                        subject = cells.nth(2).inner_text().strip() if cells.count() > 2 else ""
                        row_text = row.inner_text().strip().lower()
                        if not (number or date or subject) or any(phrase in row_text for phrase in ("no data", "no matching", "no records")):
                            continue
                        english = row.locator('a[aria-label="English"]')
                        if not english.count():
                            english = row.get_by_text("English", exact=True)
                        if not english.count():
                            errors.append(f"{category_label} {year_label}: English link missing for {number or subject}")
                            continue
                        try:
                            document_id = popup_document_id(page, english.first)
                            record = get_json(session, urljoin(BASE_URL, f"api/cbic-notification-msts/{document_id}"))
                            add_record(records, record.get("docFilePath") or record.get("contentFilePath"),
                                       "notifications", category=category_label, year=year_label,
                                       number=record.get("notificationNo") or number, date=date, subject=subject)
                        except Exception as exc:
                            errors.append(f"{category_label} {year_label} row {index + 1}: {exc}")
                        time.sleep(0.2)
                    if output_root is not None and downloaded_total is not None:
                        stage_records = {url: record for url, record in records.items() if url not in urls_before_page}
                        if stage_records:
                            count = download_all(session, stage_records, output_root, errors)
                            downloaded_total[0] += count
                            LOG.info("Customs Notifications %s %s page %d: downloaded %d PDFs",
                                     category_label, year_label, page_no, count)
                    if page_no >= total_pages:
                        break
                    if not advance_table_page(page, pane, info, size_selector):
                        errors.append(f"{category_label} {year_label}: Next was unavailable at page {page_no} of {total_pages}")
                        break
                    page_no += 1
                    if page_no > 100:
                        errors.append(f"{category_label} {year_label}: pagination limit reached")
                        break
            except Exception as exc:
                errors.append(f"{category_label} {year_label}: {exc}")
    LOG.info("Customs notifications discovered: %d", sum(r["section"] == "notifications" for r in records.values()))
    return errors


def output_path(root: Path, record: dict) -> Path:
    section = record["section"]
    if section == "notifications":
        folder = safe_name(record.get("category", "uncategorized"))
        year = safe_name(record.get("year", "unknown-year"))
        name = safe_name(str(record.get("number") or Path(record["path"]).stem).replace("/", "-"))
    else:
        folder = None
        year = safe_name(record.get("year", "unknown-year"))
        name = safe_name(Path(record["path"]).stem)
    destination = root / section
    if folder:
        destination /= folder
    return destination / year / f"{name}.pdf"


def download_all(session: requests.Session, records: dict[str, dict], root: Path, errors: list[str]) -> int:
    downloaded = 0
    reserved: set[Path] = set()
    for record in records.values():
        destination = output_path(root, record)
        if destination in reserved:
            destination = destination.with_name(f"{destination.stem} - {safe_name(record['subject'], 55)}.pdf")
        reserved.add(destination)
        if destination.exists() and destination.stat().st_size > 0:
            continue
        try:
            response = request_retry(session, "GET", record["url"], timeout=TIMEOUT_SECONDS)
            content = response.content
            if not content.startswith(b"%PDF"):
                payload, _ = json.JSONDecoder().raw_decode(response.text.lstrip())
                content = base64.b64decode(payload["data"])
            if not content.startswith(b"%PDF"):
                raise ValueError("Response was not a PDF")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            record["localPath"] = str(destination)
            downloaded += 1
        except Exception as exc:
            errors.append(f"download {record['url']}: {exc}")
            LOG.exception("Download failed for %s", record["url"])
    return downloaded


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Customs notifications and circulars from CBIC")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--from-year", type=int, default=datetime.now().year,
                        help="First year to collect (default: current year)")
    parser.add_argument("--year", help="Limit notification and circular collection to one year")
    parser.add_argument("--category", help="Limit notifications to one category")
    parser.add_argument("--notifications-only", action="store_true")
    parser.add_argument("--circulars-only", action="store_true")
    parser.add_argument("--discover-only", action="store_true", help="Write metadata without downloading PDFs")
    args = parser.parse_args()
    if args.notifications_only and args.circulars_only:
        parser.error("Choose at most one of --notifications-only and --circulars-only")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    session, jwt = new_session()
    session.headers["Authorization"] = f"Bearer {jwt}"
    records: dict[str, dict] = {}
    errors: list[str] = []
    staged_download_count = [0]
    stage_output = None if args.discover_only else root
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(ignore_https_errors=True)
        try:
            if not args.circulars_only:
                errors.extend(discover_notifications(page, session, jwt, records, args.from_year,
                                                     only_year=args.year, only_category=args.category,
                                                     output_root=stage_output,
                                                     downloaded_total=staged_download_count))
            if not args.notifications_only:
                circular_year = args.year or str(datetime.now().year)
                errors.extend(discover_circulars(page, session, records, circular_year,
                                                 output_root=stage_output,
                                                 downloaded_total=staged_download_count))
        finally:
            browser.close()
    downloaded = staged_download_count[0]
    if not args.discover_only:
        # Retry any item that was discovered but not saved during its page stage.
        missing = {url: record for url, record in records.items()
                   if not output_path(root, record).exists()}
        if missing:
            downloaded += download_all(session, missing, root, errors)
    manifest = {
        "baseUrl": BASE_URL,
        "tax": "Customs",
        "taxId": CUSTOMS_TAX_ID,
        "scope": "Customs notifications and circulars; English PDFs",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fromYear": args.from_year,
        "discovered": len(records),
        "downloaded": downloaded,
        "errors": errors,
        "files": list(records.values()),
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    LOG.info("Finished: %d discovered, %d downloaded, %d errors; output=%s", len(records), downloaded, len(errors), root)
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
