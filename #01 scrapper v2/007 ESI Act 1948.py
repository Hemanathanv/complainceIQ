import re
import time
import json
import hashlib
from datetime import datetime
"""
ESI Act 1948 / ESIC document downloader
========================================

Run from PowerShell:
    python "007 ESI Act 1948.py"

Execution order and output:
    1. downloads/Acts/       - ESI Acts and related documents
    2. downloads/Circulars/   - ESIC circular PDFs
    3. logs/                  - download logs, manifest, report, and Excel audit
"""

from pathlib import Path
import os
from urllib.parse import urljoin, urlparse, unquote

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


# ============================================================
# ESIC COMPLETE DOWNLOADER
# ============================================================

BASE_URL = "https://esic.gov.in"
CIRCULARS_URL = f"{BASE_URL}/circulars"
ACTS_URL = f"{BASE_URL}/esi-acts"

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))
ROOT = Path(os.environ.get("DOWNLOAD_BASE_PATH", SCRIPT_DIR)) / Path(__file__).stem

DOWNLOADS = ROOT / "downloads"
CIRCULARS = DOWNLOADS / "CIRCULARS"
ACTS = DOWNLOADS / "ESI_ACTS_AND_REGULATIONS"

LOGS = ROOT / "logs"

SUCCESS_LOG = LOGS / "download_log.txt"
FAILED_LOG = LOGS / "failed_downloads.txt"
REPORT_FILE = LOGS / "final_download_report.txt"
FAILED_REPORT = LOGS / "failed_pdfs.txt"
MANIFEST_FILE = LOGS / "download_manifest.json"
EXCEL_REPORT = LOGS / "downloaded_pdfs.xlsx"


# ============================================================
# ESIC DIVISIONS
# ============================================================

DIVISIONS = [
    ("18", "Actuarial"),
    ("12", "Administration"),
    ("8", "Benefit & Revenue"),
    ("7", "E.S.I.Scheme Related"),
    ("4", "Finance and Accounts"),
    ("11", "Field Offices"),
    ("14", "General"),
    ("6", "ICT (Project Panchdeep)"),
    ("17", "Legal"), 
    ("13", "National Training & H.R.D"),
    ("3", "Public Grievance"),
    ("2", "Public Relations"),
    ("5", "Property Management Division"),
    ("9", "Rajbhasha"),
    ("10", "Vigilance"),
    ("15", "Medical"),
    ("16", "Non Medical"),
]


# ============================================================
# SETTINGS
# ============================================================

HEADLESS = False

PAGE_TIMEOUT = 90_000
PDF_TIMEOUT = 180_000

PDF_RETRIES = 8

REQUEST_DELAY = 0.50


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


# ============================================================
# REPORTING
# ============================================================

GRAND_DISCOVERED = 0
GRAND_DOWNLOADED = 0
GRAND_SKIPPED = 0
GRAND_FAILED = 0

FILTER_COUNTS = {}
FAILED_PDFS = []


# ============================================================
# DIRECTORY FUNCTIONS
# ============================================================

def setup_directories():

    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    CIRCULARS.mkdir(parents=True, exist_ok=True)
    ACTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    # IMPORTANT:
    # Re-create every filter directory every time the program starts.
    for _, division_name in DIVISIONS:

        folder = CIRCULARS / division_name

        folder.mkdir(
            parents=True,
            exist_ok=True
        )

    # Acts / Rules / Regulations folders

    for name in [
        "ESI Act",
        "ESI Rules",
        "ESI Regulations",
        "Staff Conditions of Service",
        "Other",
    ]:

        folder = ACTS / name

        folder.mkdir(
            parents=True,
            exist_ok=True
        )


def ensure_folder(folder):

    """
    IMPORTANT:
    This is called immediately before every download.

    Therefore if a folder was renamed/deleted manually,
    the correct folder is automatically recreated.
    """

    folder = Path(folder)

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    return folder


# ============================================================
# LOGGING
# ============================================================

def append_log(path, message):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        path,
        "a",
        encoding="utf-8"
    ) as f:

        f.write(
            message + "\n"
        )


def log_success(message):

    append_log(
        SUCCESS_LOG,
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {message}"
    )


def log_failure(message):

    append_log(
        FAILED_LOG,
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {message}"
    )


def record_excel_download(title, destination, status, link_label="Link 1"):

    headers = [
        "S.No",
        "PDF Title",
        "Timestamp",
        "File Path",
        "Newly Downloaded",
        "Already Downloaded",
        "Link",
    ]

    if EXCEL_REPORT.exists():
        workbook = load_workbook(EXCEL_REPORT)
        worksheet = workbook.active
    else:
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Downloaded PDFs"
        worksheet.append(headers)

        for cell in worksheet[1]:
            cell.font = Font(bold=True)

    # Add the attachment/link column to older reports created by
    # previous versions of this downloader.
    if worksheet.max_column < len(headers):
        worksheet.cell(1, len(headers)).value = headers[-1]
        worksheet.cell(1, len(headers)).font = Font(bold=True)

    path_value = str(Path(destination).resolve())
    existing_row = None

    for row in worksheet.iter_rows(min_row=2):
        if row[3].value == path_value:
            existing_row = row[0].row
            break

    if existing_row is None:
        existing_row = worksheet.max_row + 1

    worksheet.cell(existing_row, 1).value = existing_row - 1
    worksheet.cell(existing_row, 2).value = title
    worksheet.cell(existing_row, 3).value = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    worksheet.cell(existing_row, 4).value = path_value
    worksheet.cell(existing_row, 5).value = "Yes" if status == "downloaded" else "No"
    worksheet.cell(existing_row, 6).value = "Yes" if status == "skipped" else "No"
    worksheet.cell(existing_row, 7).value = link_label

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    worksheet.column_dimensions["A"].width = 10
    worksheet.column_dimensions["B"].width = 60
    worksheet.column_dimensions["C"].width = 22
    worksheet.column_dimensions["D"].width = 100
    worksheet.column_dimensions["E"].width = 20
    worksheet.column_dimensions["F"].width = 20
    worksheet.column_dimensions["G"].width = 14

    workbook.save(EXCEL_REPORT)


# ============================================================
# MANIFEST
# ============================================================

def load_manifest():

    if not MANIFEST_FILE.exists():
        return {}

    try:

        with open(
            MANIFEST_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(data, dict):
            return data

    except Exception:
        pass

    return {}


MANIFEST = load_manifest()


def save_manifest():

    temporary = MANIFEST_FILE.with_suffix(
        ".tmp"
    )

    with open(
        temporary,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            MANIFEST,
            f,
            ensure_ascii=False,
            indent=2
        )

    temporary.replace(
        MANIFEST_FILE
    )


# ============================================================
# FILE HELPERS
# ============================================================

def safe_existing_file(path):

    try:

        path = Path(path)

        return (
            path.exists()
            and path.is_file()
            and path.stat().st_size > 0
        )

    except Exception:

        return False


def clean_filename(name):

    name = unquote(
        name or ""
    )

    name = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        name
    )

    name = re.sub(
        r"\s+",
        " ",
        name
    ).strip()

    if not name:

        name = "document.pdf"

    if len(name) > 180:

        name = name[:180].rstrip(
            " ."
        )

    if not name.lower().endswith(
        ".pdf"
    ):

        name += ".pdf"

    return name


def filename_from_url(url):

    path = urlparse(url).path

    filename = Path(
        unquote(path)
    ).name

    if not filename:

        filename = "document.pdf"

    return clean_filename(
        filename
    )


def filename_from_title(title, url):

    filename = filename_from_url(
        url
    )

    if filename != "document.pdf":

        return filename

    title = re.sub(
        r"\s*[-–—]?\s*PDF.*$",
        "",
        title or "",
        flags=re.I
    )

    return clean_filename(
        title
    )


# ============================================================
# URL HELPERS
# ============================================================

def absolute_url(href, base_url):

    return urljoin(
        base_url,
        href.strip()
    )


def is_pdf_link(href):

    if not href:
        return False

    value = href.lower()

    return (
        ".pdf" in value
        or "/attachments/" in value
    )


# ============================================================
# PDF VALIDATION
# ============================================================

def response_is_pdf(body, content_type):

    if not body:
        return False

    if body.startswith(
        b"%PDF"
    ):
        return True

    content_type = (
        content_type or ""
    ).lower()

    if "application/pdf" in content_type:

        return True

    return False


# ============================================================
# DESTINATION
# ============================================================

def get_destination(folder, filename, url):

    # ALWAYS recreate the folder.

    folder = ensure_folder(
        folder
    )

    filename = clean_filename(
        filename
    )

    candidate = folder / filename

    # If the exact file already exists,
    # it is safe to skip it.

    if safe_existing_file(
        candidate
    ):

        return candidate

    # Check manifest only as a secondary
    # recovery mechanism.

    old_mapping = MANIFEST.get(
        url
    )

    if old_mapping:

        old_path = Path(
            old_mapping
        )

        if safe_existing_file(
            old_path
        ):

            return old_path

    return candidate


# ============================================================
# DOWNLOAD USING BROWSER REQUEST CONTEXT
# ============================================================

def download_using_request(
    request_context,
    url,
    destination,
    referer
):

    response = None

    try:

        response = request_context.get(
            url,
            timeout=PDF_TIMEOUT,
            fail_on_status_code=False,
            max_redirects=20,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "application/pdf,"
                    "application/octet-stream,"
                    "*/*"
                ),
                "Referer": referer,
                "Connection": "keep-alive",
            }
        )

        status = response.status

        body = response.body()

        content_type = response.headers.get(
            "content-type",
            ""
        )

        if status < 200 or status >= 400:

            return False, (
                f"HTTP {status}"
            )

        if not body:

            return False, (
                "Empty response"
            )

        if not response_is_pdf(
            body,
            content_type
        ):

            first = body[:1000].lower()

            if (
                b"<html" in first
                or b"<!doctype" in first
                or b"<head" in first
            ):

                return False, (
                    "Server returned HTML instead of PDF"
                )

            # Some ESIC responses may omit
            # PDF content type but still return
            # binary PDF data.

            if not body.startswith(
                b"%PDF"
            ):

                return False, (
                    "Response is not a valid PDF"
                )

        temporary = destination.with_suffix(
            destination.suffix + ".part"
        )

        try:

            if temporary.exists():

                temporary.unlink()

        except Exception:
            pass

        with open(
            temporary,
            "wb"
        ) as f:

            f.write(body)

        if not safe_existing_file(
            temporary
        ):

            raise RuntimeError(
                "Temporary file is empty"
            )

        temporary.replace(
            destination
        )

        return True, ""

    except Exception as error:

        return False, str(error)

    finally:

        try:

            if response:
                response.dispose()

        except Exception:
            pass


# ============================================================
# DOWNLOAD ONE PDF
# ============================================================

def download_pdf(
    request_context,
    url,
    folder,
    title,
    referer,
    filter_name,
    link_label="Link 1"
):

    global GRAND_DOWNLOADED
    global GRAND_SKIPPED
    global GRAND_FAILED

    # --------------------------------------------------------
    # IMPORTANT:
    # ALWAYS create destination folder HERE.
    # --------------------------------------------------------

    folder = ensure_folder(
        folder
    )

    filename = filename_from_title(
        title,
        url
    )

    destination = get_destination(
        folder,
        filename,
        url
    )

    # --------------------------------------------------------
    # ONLY skip when the ACTUAL FILE exists.
    # --------------------------------------------------------

    if safe_existing_file(
        destination
    ):

        print(
            f"      [SKIP - FILE EXISTS] "
            f"{destination}"
        )

        GRAND_SKIPPED += 1

        FILTER_COUNTS.setdefault(
            filter_name,
            {
                "found": 0,
                "downloaded": 0,
                "skipped": 0,
                "failed": 0,
            }
        )

        FILTER_COUNTS[
            filter_name
        ]["skipped"] += 1

        MANIFEST[url] = str(
            destination
        )

        save_manifest()

        record_excel_download(
            title,
            destination,
            "skipped",
            link_label
        )

        return "skipped"

    # --------------------------------------------------------
    # DOWNLOAD RETRIES
    # --------------------------------------------------------

    last_error = "Unknown error"

    for attempt in range(
        1,
        PDF_RETRIES + 1
    ):

        print(
            f"      [DOWNLOAD "
            f"{attempt}/{PDF_RETRIES}] "
            f"{filename}"
        )

        success, error = (
            download_using_request(
                request_context,
                url,
                destination,
                referer
            )
        )

        if success:

            size_kb = (
                destination.stat().st_size
                / 1024
            )

            print(
                f"      [SUCCESS] "
                f"{destination}"
            )

            print(
                f"      Size: "
                f"{size_kb:.1f} KB"
            )

            GRAND_DOWNLOADED += 1

            FILTER_COUNTS.setdefault(
                filter_name,
                {
                    "found": 0,
                    "downloaded": 0,
                    "skipped": 0,
                    "failed": 0,
                }
            )

            FILTER_COUNTS[
                filter_name
            ]["downloaded"] += 1

            MANIFEST[url] = str(
                destination
            )

            save_manifest()

            record_excel_download(
                title,
                destination,
                "downloaded",
                link_label
            )

            log_success(
                f"{filter_name} | "
                f"{title} | "
                f"{url} | "
                f"{destination}"
            )

            return "downloaded"

        last_error = error

        print(
            f"      [RETRY] {error}"
        )

        time.sleep(
            min(
                attempt * 2,
                10
            )
        )

    # --------------------------------------------------------
    # FAILED
    # --------------------------------------------------------

    print(
        f"      [FAILED] "
        f"{filename}"
    )

    print(
        f"      Reason: "
        f"{last_error}"
    )

    GRAND_FAILED += 1

    FILTER_COUNTS.setdefault(
        filter_name,
        {
            "found": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
        }
    )

    FILTER_COUNTS[
        filter_name
    ]["failed"] += 1

    FAILED_PDFS.append(
        {
            "filter": filter_name,
            "title": title,
            "url": url,
            "destination": str(
                destination
            ),
            "error": last_error,
        }
    )

    log_failure(
        f"{filter_name} | "
        f"{title} | "
        f"{url} | "
        f"{last_error}"
    )

    return "failed"


# ============================================================
# EXTRACT PDF LINKS
# ============================================================

def extract_pdf_links(page):

    results = []

    seen = set()
    row_link_counts = {}

    links = page.locator(
        "a"
    )

    count = links.count()

    for index in range(
        count
    ):

        try:

            link = links.nth(
                index
            )

            href = link.get_attribute(
                "href"
            )

            if not href:
                continue

            url = absolute_url(
                href,
                page.url
            )

            if not is_pdf_link(
                url
            ):
                continue

            if url in seen:
                continue

            # Keep the visible website order and number attachments within
            # each circular table row as Link 1, Link 2, Link 3, etc.
            row_index = link.evaluate(
                "el => Array.from(document.querySelectorAll('tr')).indexOf(el.closest('tr'))"
            )
            row_link_counts[row_index] = row_link_counts.get(row_index, 0) + 1

            title = ""

            try:

                title = link.inner_text(
                    timeout=3000
                ).strip()

            except Exception:
                pass

            if not title:

                title = (
                    link.get_attribute(
                        "aria-label"
                    )
                    or ""
                ).strip()

            if not title:

                title = filename_from_url(
                    url
                )

            seen.add(url)

            results.append(
                {
                    "url": url,
                    "title": title,
                    "link_label": f"Link {row_link_counts[row_index]}",
                }
            )

        except Exception:

            continue

    return results


# ============================================================
# PAGE WAIT
# ============================================================

def wait_for_page(page):

    try:

        page.wait_for_load_state(
            "domcontentloaded",
            timeout=PAGE_TIMEOUT
        )

    except Exception:
        pass

    try:

        page.wait_for_load_state(
            "networkidle",
            timeout=20_000
        )

    except Exception:
        pass

    time.sleep(
        1
    )


# ============================================================
# PAGINATION
# ============================================================

def page_number_from_url(url):

    match = re.search(
        r"/page:(\d+)",
        url
    )

    if not match:
        return None

    return int(
        match.group(1)
    )


def collect_pagination_urls(page):

    results = {}

    locator = page.locator(
        '.paging-option a[href*="/circulars/index/page:"]'
    )

    count = locator.count()

    for index in range(
        count
    ):

        try:

            href = locator.nth(
                index
            ).get_attribute(
                "href"
            )

            if not href:
                continue

            url = absolute_url(
                href,
                page.url
            )

            number = page_number_from_url(
                url
            )

            if number is not None:

                results[number] = url

        except Exception:

            continue

    return results


# ============================================================
# FILTER
# ============================================================

def select_division(
    page,
    value
):

    selector = (
        "#search_by_division"
    )

    page.locator(
        selector
    ).wait_for(
        state="visible",
        timeout=30_000
    )

    page.locator(
        selector
    ).select_option(
        value=value
    )

    selected = page.locator(
        selector
    ).input_value()

    if selected != value:

        raise RuntimeError(
            f"Division selection failed: "
            f"expected {value}, "
            f"received {selected}"
        )


def click_search(page):

    button = page.locator(
        "#search_btn"
    )

    button.wait_for(
        state="visible",
        timeout=30_000
    )

    try:

        with page.expect_navigation(
            wait_until="domcontentloaded",
            timeout=60_000
        ):

            button.click()

    except PlaywrightTimeoutError:

        # The page may have changed even
        # without a navigation event.

        try:

            button.click(
                timeout=5000
            )

        except Exception:
            pass

    wait_for_page(
        page
    )


def filter_is_selected(
    page,
    value
):

    try:

        return (
            page.locator(
                "#search_by_division"
            ).input_value()
            == value
        )

    except Exception:

        return False


def open_filtered_page(
    page,
    division_value
):

    page.goto(
        CIRCULARS_URL,
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT
    )

    wait_for_page(
        page
    )

    select_division(
        page,
        division_value
    )

    print(
        "      Administration/Division selected."
    )

    click_search(
        page
    )

    if "/circulars" not in page.url:

        raise RuntimeError(
            f"Unexpected result URL: "
            f"{page.url}"
        )


# ============================================================
# PROCESS ONE FILTER
# ============================================================

def process_division(
    page,
    request_context,
    division_value,
    division_name
):

    global GRAND_DISCOVERED

    print()
    print("=" * 80)

    print(
        f"FILTER: {division_name}"
    )

    print(
        f"VALUE : {division_value}"
    )

    print("=" * 80)

    # --------------------------------------------------------
    # ALWAYS ensure the directory exists.
    # --------------------------------------------------------

    destination = ensure_folder(
        CIRCULARS / division_name
    )

    processed_urls = set()

    processed_pages = set()

    local_found = 0

    FILTER_COUNTS.setdefault(
        division_name,
        {
            "found": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
        }
    )

    try:

        open_filtered_page(
            page,
            division_value
        )

        while True:

            current_url = page.url

            if current_url in processed_pages:

                break

            processed_pages.add(
                current_url
            )

            page_number = (
                page_number_from_url(
                    current_url
                )
                or 1
            )

            print()
            print(
                "-" * 70
            )

            print(
                f"{division_name} "
                f"| PAGE {page_number}"
            )

            print(
                f"URL: {current_url}"
            )

            # ------------------------------------------------
            # Check filter.
            # ------------------------------------------------

            if not filter_is_selected(
                page,
                division_value
            ):

                print(
                    "[WARNING] Filter state "
                    "disappeared."
                )

                print(
                    "[ACTION] Re-applying "
                    "filter."
                )

                open_filtered_page(
                    page,
                    division_value
                )

                current_url = page.url

                if current_url in processed_pages:

                    break

                processed_pages.add(
                    current_url
                )

            # ------------------------------------------------
            # Extract PDFs.
            # ------------------------------------------------

            pdfs = extract_pdf_links(
                page
            )

            print(
                f"PDF links found: "
                f"{len(pdfs)}"
            )

            # ------------------------------------------------
            # Process EVERY PDF.
            # ------------------------------------------------

            for number, item in enumerate(
                pdfs,
                start=1
            ):

                url = item["url"]

                if url in processed_urls:

                    continue

                processed_urls.add(
                    url
                )

                local_found += 1

                GRAND_DISCOVERED += 1

                FILTER_COUNTS[
                    division_name
                ]["found"] += 1

                print()
                print(
                    f"[{number}/{len(pdfs)}] "
                    f"{division_name}"
                )

                print(
                    f"Title: "
                    f"{item['title']}"
                )

                print(
                    f"URL: "
                    f"{url}"
                )

                # IMPORTANT:
                # Ensure folder AGAIN before download.

                ensure_folder(
                    destination
                )

                download_pdf(
                    request_context,
                    url,
                    destination,
                    item["title"],
                    page.url,
                    division_name,
                    item.get("link_label", "Link 1")
                )

                time.sleep(
                    REQUEST_DELAY
                )

            # ------------------------------------------------
            # PAGINATION
            # ------------------------------------------------

            pagination = (
                collect_pagination_urls(
                    page
                )
            )

            current_number = (
                page_number_from_url(
                    page.url
                )
                or 1
            )

            future_pages = [
                n
                for n in sorted(
                    pagination
                )
                if n > current_number
            ]

            if future_pages:

                next_number = future_pages[0]

                next_url = pagination[
                    next_number
                ]

                print(
                    f"[NEXT] "
                    f"Page {next_number}"
                )

                try:

                    page.goto(
                        next_url,
                        wait_until="domcontentloaded",
                        timeout=PAGE_TIMEOUT
                    )

                    wait_for_page(
                        page
                    )

                    continue

                except Exception as error:

                    print(
                        f"[PAGINATION ERROR] "
                        f"{error}"
                    )

                    log_failure(
                        f"{division_name} | "
                        f"PAGINATION | "
                        f"{next_url} | "
                        f"{error}"
                    )

                    break

            # ------------------------------------------------
            # Next fallback.
            # ------------------------------------------------

            next_locator = page.locator(
                '.paging-option a[aria-label="Next"], '
                'a[rel="next"]'
            )

            next_url = None

            try:

                for index in range(
                    next_locator.count()
                ):

                    href = (
                        next_locator
                        .nth(index)
                        .get_attribute(
                            "href"
                        )
                    )

                    if not href:
                        continue

                    candidate = absolute_url(
                        href,
                        page.url
                    )

                    candidate_number = (
                        page_number_from_url(
                            candidate
                        )
                    )

                    if (
                        candidate_number
                        is not None
                        and candidate_number
                        > current_number
                    ):

                        next_url = candidate

                        break

            except Exception:
                pass

            if next_url:

                try:

                    page.goto(
                        next_url,
                        wait_until="domcontentloaded",
                        timeout=PAGE_TIMEOUT
                    )

                    wait_for_page(
                        page
                    )

                    continue

                except Exception as error:

                    log_failure(
                        f"{division_name} | "
                        f"NEXT | "
                        f"{next_url} | "
                        f"{error}"
                    )

            print(
                f"[DONE] "
                f"{division_name} "
                f"finished."
            )

            break

    except Exception as error:

        print()
        print(
            f"[FILTER ERROR] "
            f"{division_name}"
        )

        print(
            error
        )

        log_failure(
            f"{division_name} | "
            f"FILTER ERROR | "
            f"{error}"
        )

    print()
    print(
        f"SUMMARY: {division_name}"
    )

    print(
        f"Pages processed : "
        f"{len(processed_pages)}"
    )

    print(
        f"Unique PDFs     : "
        f"{local_found}"
    )

    print(
        f"Downloaded      : "
        f"{FILTER_COUNTS[division_name]['downloaded']}"
    )

    print(
        f"Skipped         : "
        f"{FILTER_COUNTS[division_name]['skipped']}"
    )

    print(
        f"Failed          : "
        f"{FILTER_COUNTS[division_name]['failed']}"
    )


# ============================================================
# ACTS / RULES / REGULATIONS
# ============================================================

def acts_document_url(link, page_url):

    """Return a real document URL from normal or JavaScript-style links."""

    candidates = []

    for attribute in [
        "href",
        "data-href",
        "data-url",
        "data-download",
        "data-file",
    ]:

        try:
            value = link.get_attribute(attribute)
        except Exception:
            value = None

        if value:
            candidates.append(value)

    try:
        onclick = link.get_attribute("onclick") or ""
    except Exception:
        onclick = ""

    # Some ESIC entries use window.open('...') instead of a usable href.
    candidates.extend(
        re.findall(r"['\"]([^'\"]+)['\"]", onclick)
    )

    for candidate in candidates:

        candidate = candidate.strip()

        if not candidate or candidate.lower().startswith("javascript:"):
            continue

        url = absolute_url(candidate, page_url)

        if is_pdf_link(url):
            return url

    return None


def acts_section_text(link):

    """Find the local Acts/Services heading, never the entire page text."""

    try:

        return link.evaluate(
            """
            (el) => {
                const text = (node) => (node?.innerText || node?.textContent || "")
                    .replace(/\\s+/g, " ").trim();
                const heading = "h1,h2,h3,h4,h5,h6,.card-header,.accordion-header," +
                    ".panel-heading,.title,.heading";

                let node = el;
                for (let depth = 0; depth < 6 && node; depth++, node = node.parentElement) {
                    const localHeading = node.querySelector(heading);
                    if (localHeading && localHeading !== el) {
                        const value = text(localHeading);
                        if (value) return value;
                    }

                    let previous = node.previousElementSibling;
                    for (let count = 0; previous && count < 12; count++, previous = previous.previousElementSibling) {
                        if (previous.matches(heading)) return text(previous);
                        const previousHeading = previous.querySelector(heading);
                        if (previousHeading) return text(previousHeading);
                    }
                }

                return "";
            }
            """
        )

    except Exception:

        return ""


def classify_act(
    title,
    url,
    section
):

    text = (
        f"{title} "
        f"{url} "
        f"{section}"
    ).lower()

    compact = re.sub(
        r"[^a-z0-9]+",
        " ",
        text
    )

    # Staff Conditions FIRST

    if (
        "staff" in compact
        and "condition" in compact
        and "service" in compact
    ):

        return (
            ACTS /
            "Staff Conditions of Service"
        )

    if (
        "staff conditions" in compact
        or "conditions of service" in compact
        or "staff and conditions" in compact
    ):

        return (
            ACTS /
            "Staff Conditions of Service"
        )

    # Regulations

    if "regulation" in compact:

        return (
            ACTS /
            "ESI Regulations"
        )

    # Rules

    if re.search(
        r"\brules?\b",
        compact
    ):

        return (
            ACTS /
            "ESI Rules"
        )

    # Act

    if (
        "employees state insurance act"
        in compact
        or "esi act"
        in compact
    ):

        return (
            ACTS /
            "ESI Act"
        )

    return (
        ACTS /
        "Other"
    )


def extract_acts_links(
    page
):

    results = []

    seen = set()

    links = page.locator(
        "a"
    )

    count = links.count()

    print(
        f"Acts page anchors inspected: "
        f"{count}"
    )

    for index in range(
        count
    ):

        try:

            link = links.nth(
                index
            )

            url = acts_document_url(
                link,
                page.url
            )

            if not url:
                continue

            if url in seen:
                continue

            title = ""

            try:

                title = link.inner_text(
                    timeout=3000
                ).strip()

            except Exception:
                pass

            if not title:

                title = (
                    link.get_attribute(
                        "aria-label"
                    )
                    or ""
                ).strip()

            if not title:

                title = filename_from_url(
                    url
                )

            section = acts_section_text(
                link
            )

            category = classify_act(
                title,
                url,
                section
            )

            seen.add(
                url
            )

            results.append(
                {
                    "url": url,
                    "title": title,
                    "section": section,
                    "category": category,
                }
            )

        except Exception:

            continue

    return results


def process_acts(
    page,
    request_context
):

    global GRAND_DISCOVERED

    print()
    print("=" * 80)

    print(
        "PHASE 2 - "
        "ESI ACTS / RULES / REGULATIONS / "
        "STAFF CONDITIONS"
    )

    print("=" * 80)

    try:

        # Make sure all folders exist.

        for folder_name in [
            "ESI Act",
            "ESI Rules",
            "ESI Regulations",
            "Staff Conditions of Service",
            "Other",
        ]:

            ensure_folder(
                ACTS / folder_name
            )

        page.goto(
            ACTS_URL,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT
        )

        wait_for_page(
            page
        )

        print(
            f"Acts URL: "
            f"{page.url}"
        )

        items = extract_acts_links(
            page
        )

        print()
        print(
            f"TOTAL ACT/RULE/REGULATION "
            f"PDF LINKS: {len(items)}"
        )

        for number, item in enumerate(
            items,
            start=1
        ):

            category = item[
                "category"
            ]

            category_name = (
                category.name
            )

            GRAND_DISCOVERED += 1

            FILTER_COUNTS.setdefault(
                category_name,
                {
                    "found": 0,
                    "downloaded": 0,
                    "skipped": 0,
                    "failed": 0,
                }
            )

            FILTER_COUNTS[
                category_name
            ]["found"] += 1

            print()
            print(
                "-" * 80
            )

            print(
                f"ACT/RULE PDF "
                f"{number}/{len(items)}"
            )

            print(
                f"Category: "
                f"{category_name}"
            )

            print(
                f"Title: "
                f"{item['title']}"
            )

            print(
                f"URL: "
                f"{item['url']}"
            )

            # VERY IMPORTANT:
            # Re-create category folder immediately.

            ensure_folder(
                category
            )

            download_pdf(
                request_context,
                item["url"],
                category,
                item["title"],
                page.url,
                category_name
            )

            time.sleep(
                REQUEST_DELAY
            )

        print()
        print(
            "ACTS / RULES / REGULATIONS "
            "PROCESSING FINISHED."
        )

    except Exception as error:

        print()
        print(
            "[ACTS ERROR]"
        )

        print(
            error
        )

        log_failure(
            f"ACTS SECTION ERROR | "
            f"{error}"
        )


# ============================================================
# FINAL REPORT
# ============================================================

def write_report():

    lines = []

    lines.append(
        "=" * 90
    )

    lines.append(
        "ESIC COMPLETE DOWNLOAD REPORT"
    )

    lines.append(
        "=" * 90
    )

    lines.append("")

    lines.append(
        "PER-FILTER COUNTS"
    )

    lines.append(
        "-" * 90
    )

    for name, values in FILTER_COUNTS.items():

        lines.append(
            f"{name}: "
            f"FOUND={values['found']} | "
            f"DOWNLOADED={values['downloaded']} | "
            f"SKIPPED={values['skipped']} | "
            f"FAILED={values['failed']}"
        )

    lines.append(
        "-" * 90
    )

    lines.append(
        f"GRAND TOTAL PDFS DISCOVERED: "
        f"{GRAND_DISCOVERED}"
    )

    lines.append(
        f"DOWNLOADED THIS RUN: "
        f"{GRAND_DOWNLOADED}"
    )

    lines.append(
        f"ALREADY EXISTED / SKIPPED: "
        f"{GRAND_SKIPPED}"
    )

    lines.append(
        f"FAILED: "
        f"{GRAND_FAILED}"
    )

    lines.append(
        "=" * 90
    )

    REPORT_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    # --------------------------------------------------------
    # FAILED PDF REPORT
    # --------------------------------------------------------

    failed_lines = []

    failed_lines.append(
        "=" * 90
    )

    failed_lines.append(
        "FAILED ESIC PDF DOWNLOADS"
    )

    failed_lines.append(
        "=" * 90
    )

    failed_lines.append("")

    if FAILED_PDFS:

        for number, item in enumerate(
            FAILED_PDFS,
            start=1
        ):

            failed_lines.append(
                f"[{number}]"
            )

            failed_lines.append(
                f"Filter: "
                f"{item['filter']}"
            )

            failed_lines.append(
                f"Title: "
                f"{item['title']}"
            )

            failed_lines.append(
                f"URL: "
                f"{item['url']}"
            )

            failed_lines.append(
                f"Destination: "
                f"{item['destination']}"
            )

            failed_lines.append(
                f"Error: "
                f"{item['error']}"
            )

            failed_lines.append(
                "-" * 90
            )

    else:

        failed_lines.append(
            "NO FAILED PDF DOWNLOADS."
        )

    FAILED_REPORT.write_text(
        "\n".join(
            failed_lines
        ),
        encoding="utf-8"
    )

    print()
    print("=" * 90)

    print(
        "FINAL ESIC DOWNLOAD REPORT"
    )

    print("=" * 90)

    for name, values in FILTER_COUNTS.items():

        print(
            f"{name:<35} "
            f"FOUND={values['found']:>4} | "
            f"DOWNLOADED={values['downloaded']:>4} | "
            f"SKIPPED={values['skipped']:>4} | "
            f"FAILED={values['failed']:>4}"
        )

    print("-" * 90)

    print(
        f"PDFs discovered : "
        f"{GRAND_DISCOVERED}"
    )

    print(
        f"Downloaded      : "
        f"{GRAND_DOWNLOADED}"
    )

    print(
        f"Already existed : "
        f"{GRAND_SKIPPED}"
    )

    print(
        f"Failed          : "
        f"{GRAND_FAILED}"
    )

    print("=" * 90)

    print(
        f"Download folder:"
        f"\n{DOWNLOADS.resolve()}"
    )

    print(
        f"\nReport:"
        f"\n{REPORT_FILE.resolve()}"
    )

    print(
        f"\nFailed PDFs:"
        f"\n{FAILED_REPORT.resolve()}"
    )

    print(
        f"\nExcel report:"
        f"\n{EXCEL_REPORT.resolve()}"
    )

    print("=" * 90)


# ============================================================
# MAIN
# ============================================================

def main():

    setup_directories()

    print()
    print("=" * 80)

    print(
        "ESIC COMPLETE PDF DOWNLOADER"
    )

    print(
        "NO PDF COUNT LIMIT"
    )

    print("=" * 80)

    print()
    print(
        "Download root:"
    )

    print(
        DOWNLOADS.resolve()
    )

    print()
    print(
        "Circular folders:"
    )

    print(
        CIRCULARS.resolve()
    )

    print()
    print(
        "Acts folders:"
    )

    print(
        ACTS.resolve()
    )

    print()
    print(
        f"PDF retries: "
        f"{PDF_RETRIES}"
    )

    print(
        "SSL certificate errors: IGNORED"
    )

    print("=" * 80)

    with sync_playwright() as pw:

        # ----------------------------------------------------
        # START CHROMIUM
        # ----------------------------------------------------

        print(
            "[1] Starting Chromium..."
        )

        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--ignore-certificate-errors",
                "--allow-running-insecure-content",
            ]
        )

        # ----------------------------------------------------
        # BROWSER CONTEXT
        # ----------------------------------------------------

        print(
            "[2] Creating browser context..."
        )

        browser_context = (
            browser.new_context(
                ignore_https_errors=True,
                accept_downloads=True,
                user_agent=USER_AGENT,
                viewport={
                    "width": 1600,
                    "height": 1000,
                }
            )
        )

        page = (
            browser_context.new_page()
        )

        # ----------------------------------------------------
        # CRITICAL:
        #
        # Use the REQUEST CONTEXT belonging to the SAME
        # browser context.
        #
        # This preserves ESIC session cookies.
        # ----------------------------------------------------

        request_context = (
            browser_context.request
        )

        try:

            print(
                "[3] Opening ESIC..."
            )

            page.goto(
                BASE_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT
            )

            wait_for_page(
                page
            )

            print(
                "[OK] ESIC opened."
            )

            print(
                f"Title: "
                f"{page.title()}"
            )

            print(
                f"URL: "
                f"{page.url}"
            )

            # =================================================
            # PHASE 1
            # =================================================

            print()
            print("=" * 80)

            print(
                "PHASE 1 - "
                "ALL ESIC CIRCULAR FILTERS"
            )

            print("=" * 80)

            for value, name in DIVISIONS:

                process_division(
                    page,
                    request_context,
                    value,
                    name
                )

            # =================================================
            # PHASE 2
            # =================================================

            process_acts(
                page,
                request_context
            )

            # =================================================
            # REPORT
            # =================================================

            write_report()

            print()
            print(
                "ALL ESIC PROCESSING FINISHED."
            )

        except KeyboardInterrupt:

            print()
            print(
                "Program interrupted by user."
            )

            log_failure(
                "PROGRAM INTERRUPTED"
            )

        except Exception as error:

            print()
            print(
                "[MAIN ERROR]"
            )

            print(
                error
            )

            log_failure(
                f"MAIN ERROR | {error}"
            )

        finally:

            # Always write the report even if
            # something fails.

            try:
                write_report()
            except Exception:
                pass

            try:
                browser_context.close()
            except Exception:
                pass

            try:
                browser.close()
            except Exception:
                pass

            print()
            print(
                "Browser closed."
            )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
