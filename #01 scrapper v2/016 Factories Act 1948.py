"""
Labour Ministry Acts and Orders document downloader
====================================================

Run from PowerShell:
    python "016 Factories Act 1948.py"

Execution order and output:
    1. Labour_Ministry_Documents/Acts and Policies/
    2. Labour_Ministry_Documents/Orders and Notices/
    3. Labour_Ministry_Documents/Labour_Ministry_Download_Report.xlsx
"""

import re
import os
import json
import random
import time
import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ============================================================
# EXCEL REPORT MODULE
# ============================================================
try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    _EXCEL_AVAILABLE = True
except ImportError:
    _EXCEL_AVAILABLE = False
    print("[WARNING] openpyxl not installed - Excel report will be skipped.")
    print("          Run: pip install openpyxl")

# Global log list - each entry: {sno, title, timestamp, path}
_download_log = []
_log_counter = 0


def _log_download(title, file_path, newly_downloaded, already_downloaded):
    """Append one row to the global download log."""
    global _log_counter
    _log_counter += 1
    _download_log.append({
        "sno":       _log_counter,
        "title":     title,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "path":      str(file_path),
        "newly_downloaded": newly_downloaded,
        "already_downloaded": already_downloaded,
    })


def export_to_excel():
    """
    Write _download_log to a formatted .xlsx in the DOWNLOAD_ROOT folder.

    Color fix: openpyxl needs 8-char ARGB hex (e.g. 'FF1F4E79').
    Using 6-char hex makes alpha=00 (transparent) - colours appear blank.
    """
    if not _EXCEL_AVAILABLE:
        print("[SKIP] Excel export skipped - openpyxl not available.")
        return
    if not _download_log:
        print("[INFO] No downloads recorded - Excel report not created.")
        return

    report_path = DOWNLOAD_ROOT / "Labour_Ministry_Download_Report.xlsx"

    try:
        wb = Workbook()
        ws = wb.active
        ws.title = "Download Report"

        # ── Styles (ALL colors: 8-char ARGB, FF = fully opaque) ──────────────
        HDR_FILL    = PatternFill(fill_type="solid", fgColor="FF1F4E79")  # dark navy
        HDR_FONT    = Font(bold=True, color="FFFFFFFF", size=11)           # white bold
        DATA_FONT   = Font(size=10)
        ALT_FILL    = PatternFill(fill_type="solid", fgColor="FFEBF3FB")  # light blue

        BORDER_SIDE = Side(style="thin", color="FFAAAAAA")
        CELL_BORDER = Border(
            left=BORDER_SIDE, right=BORDER_SIDE,
            top=BORDER_SIDE,  bottom=BORDER_SIDE,
        )

        C_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
        C_LEFT   = Alignment(horizontal="left",   vertical="center", wrap_text=True)

        HEADERS    = ["S.No", "PDF Title",  "Timestamp",  "File Path", "Newly Downloaded", "Already Downloaded"]
        COL_WIDTHS = [7,       65,            22,           90,           20,                 20]
        COL_ALIGNS = [C_CENTER, C_LEFT,       C_CENTER,    C_LEFT,       C_CENTER,           C_CENTER]

        # ── Header row ────────────────────────────────────────────────────────
        for col_idx, (header, width) in enumerate(zip(HEADERS, COL_WIDTHS), start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.fill      = HDR_FILL
            cell.font      = HDR_FONT
            cell.alignment = C_CENTER
            cell.border    = CELL_BORDER
            ws.column_dimensions[cell.column_letter].width = width
        ws.row_dimensions[1].height = 22

        # ── Data rows ─────────────────────────────────────────────────────────
        for row_idx, entry in enumerate(_download_log, start=2):
            row_values = [entry["sno"], entry["title"], entry["timestamp"], entry["path"], entry["newly_downloaded"], entry["already_downloaded"]]
            for col_idx, (value, align) in enumerate(zip(row_values, COL_ALIGNS), start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.font      = DATA_FONT
                cell.alignment = align
                cell.border    = CELL_BORDER
                if row_idx % 2 == 0:
                    cell.fill = ALT_FILL

        # ── Freeze header + auto-filter ───────────────────────────────────────
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = "A1:F{}".format(len(_download_log) + 1)

        wb.save(report_path)
        print("\n[EXCEL REPORT] Saved  ->  {}".format(report_path.resolve()))
        print("[EXCEL REPORT] Total rows written: {}".format(len(_download_log)))

    except Exception as err:
        print("\n[EXCEL ERROR] Failed to generate report: {}".format(err))
        print("              Tip: make sure the file is not already open in Excel.")

# ============================================================
# END OF EXCEL REPORT MODULE
# ============================================================


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.labour.gov.in"

ACTS_URL = f"{BASE_URL}/documents/acts-and-policies"
ORDERS_URL = f"{BASE_URL}/documents/orders-and-notices"

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))
DOWNLOAD_ROOT = Path(os.environ.get("DOWNLOAD_BASE_PATH", SCRIPT_DIR)) / Path(__file__).stem
RUNTIME_DIR = DOWNLOAD_ROOT / "logs"
RUN_STATUS = RUNTIME_DIR / "run_status.json"
RUNTIME_LOG = RUNTIME_DIR / "runtime.log"
RESILIENCE_ATTEMPTS = int(os.environ.get("SCRAPER_MAX_ATTEMPTS", "3"))
REQUEST_DELAY_SECONDS = float(os.environ.get("SCRAPER_REQUEST_DELAY_SECONDS", "1.0"))
BLOCK_COOLDOWN = float(os.environ.get("SCRAPER_BLOCK_COOLDOWN_SECONDS", "300"))


class SiteAccessBlocked(RuntimeError):
    pass


BLOCK_DETECTED = ""


def check_response_status(status, url=""):
    global BLOCK_DETECTED
    if status in {403, 429}:
        BLOCK_DETECTED = f"HTTP {status} from {url}"
        raise SiteAccessBlocked(BLOCK_DETECTED)


def resilient_get(session, url, **kwargs):
    last_error = None
    for attempt in range(1, RESILIENCE_ATTEMPTS + 1):
        time.sleep(REQUEST_DELAY_SECONDS + random.uniform(0, 0.35))
        try:
            response = session.get(url, **kwargs)
            check_response_status(response.status_code, url)
            if response.status_code < 500 or attempt == RESILIENCE_ATTEMPTS:
                return response
            last_error = RuntimeError(f"HTTP {response.status_code} from {url}")
        except SiteAccessBlocked:
            raise
        except Exception as error:
            last_error = error
        if attempt < RESILIENCE_ATTEMPTS:
            time.sleep(min(120, 5 * (2 ** (attempt - 1))) + random.uniform(0, 2))
    raise last_error or RuntimeError(f"Request failed: {url}")


def write_run_status(state, message="", attempt=0):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {"script": Path(__file__).name, "state": state, "attempt": attempt,
               "message": str(message), "timestamp": stamp}
    temporary = RUN_STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(RUN_STATUS)
    with RUNTIME_LOG.open("a", encoding="utf-8") as stream:
        stream.write(f"{stamp} | {state} | attempt={attempt} | {message}\n")


def guarded_main(entrypoint):
    global BLOCK_DETECTED
    for attempt in range(1, RESILIENCE_ATTEMPTS + 1):
        BLOCK_DETECTED = ""
        write_run_status("running", attempt=attempt)
        try:
            result = entrypoint()
            if BLOCK_DETECTED:
                raise SiteAccessBlocked(BLOCK_DETECTED)
            write_run_status("completed", attempt=attempt)
            return result
        except KeyboardInterrupt:
            write_run_status("stopped", "Stopped by user", attempt)
            raise
        except SiteAccessBlocked as error:
            write_run_status("blocked", error, attempt)
            if attempt == RESILIENCE_ATTEMPTS:
                raise
            time.sleep(BLOCK_COOLDOWN + random.uniform(0, 5))
        except Exception as error:
            write_run_status("error", error, attempt)
            if attempt == RESILIENCE_ATTEMPTS:
                raise
            time.sleep(min(120, 5 * (2 ** (attempt - 1))) + random.uniform(0, 2))

# Keep False while testing so you can watch the browser.
HEADLESS = False

PAGE_TIMEOUT = 60000
DOWNLOAD_TIMEOUT = 120000

WAIT_AFTER_PAGE_LOAD = 2.0
WAIT_AFTER_CLICK = 1.5
MAX_RETRIES = 3

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)


# ============================================================
# HELPERS
# ============================================================

def clean_filename(name: str) -> str:
    """Make a safe Windows filename."""
    name = (name or "").strip()
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1F]", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(". ")

    if not name:
        name = "document"

    # Windows path/name safety.
    if name.upper() in {"CON", "PRN", "AUX", "NUL"}:
        name = f"_{name}"

    return name[:180]


def normalize_url(url: str) -> str:
    if not url:
        return ""
    return urljoin(BASE_URL, url.strip())


def is_pdf_url(url: str) -> bool:
    if not url:
        return False

    path = urlparse(url).path.lower()
    return path.endswith(".pdf") or ".pdf" in path


def filename_from_url(url: str) -> str:
    name = Path(urlparse(url).path).name or "document.pdf"

    if not name.lower().endswith(".pdf"):
        name += ".pdf"

    return clean_filename(name)


def filename_from_title(title: str, url: str) -> str:
    title = clean_filename(title)

    if not title:
        return filename_from_url(url)

    if not title.lower().endswith(".pdf"):
        title += ".pdf"

    return title


def unique_filename(folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)

    target = folder / clean_filename(filename)

    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix

    counter = 2
    while True:
        candidate = folder / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def category_folder_name(title: str, fallback_url: str = "") -> str:
    """Convert 'View 5 Industrial Relations (IR Codes)' to a folder name."""
    name = title or ""

    name = re.sub(r"^\s*View\s+\d+\s*", "", name, flags=re.I)
    name = re.sub(r"^\s*View\s+All\s*", "", name, flags=re.I)
    name = re.sub(r"\s*View\s+All\s*$", "", name, flags=re.I)
    name = name.strip()

    if not name:
        path_name = Path(urlparse(fallback_url).path).name
        name = path_name or "Category"

    return clean_filename(name)


# ============================================================
# PDF DOWNLOADER
# ============================================================

class PDFDownloader:
    """
    Downloads public PDFs directly from their href.

    The Labour Ministry document buttons point to static PDF files,
    so downloading the href is more reliable than opening the PDF
    in a new browser tab.
    """

    def __init__(self, root_folder: Path):
        self.root_folder = root_folder
        self.downloaded_urls = set()
        self.downloaded_hashes = set()

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/pdf,*/*",
            }
        )

    def download(self, url: str, folder: Path, title: str = "") -> bool:
        url = normalize_url(url)

        if not url or not is_pdf_url(url):
            return False

        if url in self.downloaded_urls:
            print(f"[SKIP URL] {url}")
            return False

        folder.mkdir(parents=True, exist_ok=True)

        filename = (
            filename_from_title(title, url)
            if title
            else filename_from_url(url)
        )

        existing_destination = folder / clean_filename(filename)
        if existing_destination.exists():
            print(f"[SKIP EXISTING] {existing_destination}")
            _log_download(
                title if title else existing_destination.name,
                str(existing_destination),
                "No",
                "Yes",
            )
            self.downloaded_urls.add(url)
            return False

        destination = unique_filename(folder, filename)
        temp_destination = destination.with_suffix(destination.suffix + ".part")

        print()
        print("-" * 90)
        print("[PDF]")
        print(f"URL      : {url}")
        print(f"Filename : {destination.name}")

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = resilient_get(self.session,
                    url,
                    timeout=DOWNLOAD_TIMEOUT,
                    stream=True,
                    allow_redirects=True,
                )
                response.raise_for_status()

                content_type = response.headers.get("Content-Type", "").lower()
                content_length = response.headers.get("Content-Length", "")

                sha256 = hashlib.sha256()
                total_bytes = 0

                with open(temp_destination, "wb") as file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue

                        file.write(chunk)
                        sha256.update(chunk)
                        total_bytes += len(chunk)

                # Basic validation. A PDF should begin with %PDF.
                with open(temp_destination, "rb") as check_file:
                    header = check_file.read(5)

                if header != b"%PDF-":
                    raise ValueError(
                        f"Downloaded response does not look like a PDF "
                        f"(Content-Type: {content_type})"
                    )

                file_hash = sha256.hexdigest()

                # Duplicate content detection.
                if file_hash in self.downloaded_hashes:
                    print("[SKIP HASH] Duplicate PDF content detected.")
                    temp_destination.unlink(missing_ok=True)
                    self.downloaded_urls.add(url)
                    return False

                temp_destination.replace(destination)

                self.downloaded_hashes.add(file_hash)
                self.downloaded_urls.add(url)

                print(f"[SUCCESS] {destination}")
                print(f"[SIZE] {total_bytes / 1024:.2f} KB")
                print(f"[CONTENT-TYPE] {content_type}")
                if content_length:
                    print(f"[SERVER SIZE] {content_length} bytes")

                _log_download(
                    title if title else destination.name,
                    str(destination),
                    "Yes",
                    "No",
                )

                return True

            except SiteAccessBlocked:
                raise

            except Exception as exc:
                print(
                    f"[ERROR] Download attempt "
                    f"{attempt}/{MAX_RETRIES}: {exc}"
                )

                try:
                    temp_destination.unlink(missing_ok=True)
                except Exception:
                    pass

                if attempt < MAX_RETRIES:
                    time.sleep(2 * attempt)

        print(f"[FAILED] Could not download: {url}")
        return False


# ============================================================
# LABOUR MINISTRY SCRAPER
# ============================================================

class LabourMinistryScraper:
    def __init__(self, page, downloader):
        self.page = page
        self.downloader = downloader
        self.page.set_default_timeout(PAGE_TIMEOUT)

    # ----------------------------------------------------------
    # BASIC NAVIGATION
    # ----------------------------------------------------------

    def open_homepage(self):
        print()
        print("=" * 100)
        print("OPENING LABOUR MINISTRY WEBSITE")
        print("=" * 100)
        print(f"[HOME] {BASE_URL}")

        self.page.goto(
            BASE_URL,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        self.page.wait_for_timeout(int(WAIT_AFTER_PAGE_LOAD * 1000))

    def select_english(self):
        """
        Select English only if the English option is available and
        not already selected.
        """
        print()
        print("=" * 100)
        print("SELECTING ENGLISH")
        print("=" * 100)

        selectors = [
            'li.language-option[data-value="en"]',
            'li[role="option"][data-value="en"]',
            'li.dont-translate.language-option',
        ]

        for selector in selectors:
            try:
                option = self.page.locator(selector).first

                if option.count() == 0 or not option.is_visible():
                    continue

                aria_selected = option.get_attribute("aria-selected")

                if aria_selected == "true":
                    print("[LANGUAGE] English is already selected.")
                    return True

                print(f"[LANGUAGE] Found English option: {selector}")
                option.click(force=True)
                self.page.wait_for_timeout(2000)
                print("[LANGUAGE] English selected.")
                return True

            except Exception:
                continue

        try:
            english = self.page.get_by_text("English", exact=True).first

            if english.count() and english.is_visible():
                english.click(force=True)
                self.page.wait_for_timeout(2000)
                print("[LANGUAGE] English selected using text.")
                return True
        except Exception:
            pass

        print("[LANGUAGE] English selector not found.")
        print("[LANGUAGE] Continuing; page may already be in English.")
        return False

    def open_documents_menu(self):
        """
        Opens Documents if the menu is present.

        This is not required for scraping because the actual document
        URLs are known, but it keeps the requested navigation flow.
        """
        print()
        print("=" * 100)
        print("OPENING DOCUMENTS MENU")
        print("=" * 100)

        selectors = [
            'p.h3.mb-0.text-capitalize',
            'text=Documents',
        ]

        for selector in selectors:
            try:
                element = self.page.locator(selector).first

                if element.count() and element.is_visible():
                    element.click(force=True)
                    self.page.wait_for_timeout(1000)
                    print("[DOCUMENTS] Documents menu opened.")
                    return True
            except Exception:
                continue

        print("[DOCUMENTS] Menu was not opened; continuing.")
        return False

    def open_section(self, url: str, name: str):
        print()
        print("=" * 100)
        print(f"OPENING {name.upper()}")
        print("=" * 100)
        print(f"[URL] {url}")

        self.page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        self.page.wait_for_timeout(int(WAIT_AFTER_PAGE_LOAD * 1000))

        self.wait_for_document_table()

    def wait_for_document_table(self):
        try:
            self.page.locator('[role="table"]').first.wait_for(
                state="visible",
                timeout=PAGE_TIMEOUT,
            )
        except Exception:
            # Some rendering variants may not expose the table role
            # immediately. Give React a little extra time.
            self.page.wait_for_timeout(2000)

    # ----------------------------------------------------------
    # TABLE / CONTENT EXTRACTION
    # ----------------------------------------------------------

    def get_document_table(self):
        """
        Return the document table if present.

        IMPORTANT:
        All PDF/category searches are scoped to this table. This is
        what prevents hidden/unrelated document links elsewhere on
        the page from being collected.
        """
        table = self.page.locator('[role="table"]').first

        if table.count():
            return table

        # Fallback for unexpected markup.
        return self.page.locator("body")

    def get_pdf_links(self):
        """
        Get ONLY PDF links from the currently displayed document table.
        """
        table = self.get_document_table()

        selectors = [
            'a[type="pdf"]',
            'a.download-btn[href*=".pdf"]',
            'a[href$=".pdf"]',
            'a[href*=".pdf?"]',
        ]

        links = None

        for selector in selectors:
            candidate = table.locator(selector)

            if candidate.count():
                links = candidate
                break

        if links is None:
            return []

        results = []
        seen_urls = set()

        for i in range(links.count()):
            try:
                link = links.nth(i)

                if not link.is_visible():
                    continue

                href = link.get_attribute("href")
                if not href:
                    continue

                href = normalize_url(href)

                if not is_pdf_url(href):
                    continue

                if href in seen_urls:
                    continue

                title = ""

                # First preference: document row title.
                try:
                    row = link.locator(
                        'xpath=ancestor::*[@role="row"][1]'
                    ).first

                    if row.count():
                        # The supplied Labour site HTML uses p.mb-0
                        # for the document title.
                        title_element = row.locator("p.mb-0").first

                        if title_element.count():
                            title = title_element.inner_text().strip()

                        if not title:
                            title = row.inner_text().strip().splitlines()[0]
                except Exception:
                    pass

                if not title:
                    title = link.get_attribute("aria-label") or ""

                if not title:
                    title = link.get_attribute("title") or ""

                if not title:
                    title = filename_from_url(href)

                seen_urls.add(href)
                results.append(
                    {
                        "url": href,
                        "title": title.strip(),
                    }
                )

            except Exception as exc:
                print(f"[WARNING] Could not inspect PDF link: {exc}")

        print(f"[PDF LINKS] {len(results)} PDF link(s) on current page.")
        return results

    def get_category_links(self, section_url: str):
        """
        Get ONLY category/View All links from the current document table.

        A category must:
          - be inside the current document table
          - point to /documents/
          - NOT point to a PDF
          - NOT be the current main section
          - have View/View All semantics in title/text

        This prevents the old bug where unrelated document links were
        interpreted as categories.
        """
        table = self.get_document_table()

        links = table.locator('a[href*="/documents/"]')
        results = []
        seen = set()

        current_path = urlparse(section_url).path.rstrip("/")

        for i in range(links.count()):
            try:
                link = links.nth(i)

                if not link.is_visible():
                    continue

                href = link.get_attribute("href")
                if not href:
                    continue

                href = normalize_url(href)
                parsed = urlparse(href)

                if not parsed.path.startswith("/documents/"):
                    continue

                if is_pdf_url(href):
                    continue

                if parsed.path.rstrip("/") == current_path:
                    continue

                title_attr = link.get_attribute("title") or ""
                aria_label = link.get_attribute("aria-label") or ""

                try:
                    visible_text = link.inner_text().strip()
                except Exception:
                    visible_text = ""

                combined = " ".join(
                    part.strip()
                    for part in [title_attr, aria_label, visible_text]
                    if part.strip()
                )

                # The category links supplied in the HTML use "View All".
                # Accept "View" as a fallback for markup variants.
                if not re.search(r"\bview\s*(?:all)?\b", combined, re.I):
                    continue

                if href in seen:
                    continue

                category_title = title_attr or aria_label or visible_text
                category_title = re.sub(
                    r"\s+",
                    " ",
                    category_title,
                ).strip()

                if not category_title:
                    category_title = Path(parsed.path).name.replace("-", " ")

                seen.add(href)

                results.append(
                    {
                        "url": href,
                        "title": category_title,
                    }
                )

            except Exception as exc:
                print(f"[WARNING] Could not inspect category: {exc}")

        print(f"[CATEGORIES] {len(results)} category link(s) on current page.")
        return results

    # ----------------------------------------------------------
    # PAGINATION
    # ----------------------------------------------------------

    def get_next_button(self):
        """
        Find the pagination Next button.

        The supplied Labour HTML uses:
          button.button-item.next
          aria-label="Next" or "Next page"
          aria-disabled="true/false"
        """
        selectors = [
            'button.button-item.next[aria-label="Next"]',
            'button.button-item.next[aria-label="Next page"]',
            'button.next[title="Next"]',
            'button[aria-label="Next"]',
            'button[aria-label="Next page"]',
            'button.next',
        ]

        for selector in selectors:
            try:
                button = self.page.locator(selector).first
                if button.count():
                    return button
            except Exception:
                continue

        return None

    def is_next_disabled(self, button) -> bool:
        if button is None:
            return True

        try:
            if button.is_disabled():
                return True
        except Exception:
            pass

        for attribute in ("aria-disabled", "disabled"):
            try:
                value = button.get_attribute(attribute)

                if attribute == "aria-disabled" and value == "true":
                    return True

                if attribute == "disabled" and value is not None:
                    return True
            except Exception:
                pass

        return False

    def table_fingerprint(self):
        """
        Fingerprint the currently displayed document table.

        This is more reliable than depending on the URL because the
        Labour site can change pagination through React without changing
        the browser URL.
        """
        try:
            table = self.get_document_table()
            text = table.inner_text(timeout=5000)
            return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
        except Exception:
            return ""

    def click_next_and_wait(self):
        """
        Click Next and wait until the table contents actually change.
        """
        button = self.get_next_button()

        if button is None:
            print("[PAGINATION] Next button not found.")
            return False

        if self.is_next_disabled(button):
            print("[PAGINATION] Next button is disabled. Last page reached.")
            return False

        old_fingerprint = self.table_fingerprint()
        old_url = self.page.url

        try:
            button.scroll_into_view_if_needed()
        except Exception:
            pass

        try:
            button.click(force=True)
        except Exception as exc:
            print(f"[PAGINATION] Next click failed: {exc}")
            return False

        # Wait for React to replace the table contents.
        changed = False

        try:
            self.page.wait_for_function(
                """
                ({oldFingerprint, oldUrl}) => {
                    const table = document.querySelector('[role="table"]');
                    const text = table ? table.innerText : "";
                    let hash = 0;

                    for (let i = 0; i < text.length; i++) {
                        hash = ((hash << 5) - hash) + text.charCodeAt(i);
                        hash |= 0;
                    }

                    return (
                        location.href !== oldUrl ||
                        String(hash) !== oldFingerprint
                    );
                }
                """,
                {
                    "oldFingerprint": old_fingerprint,
                    "oldUrl": old_url,
                },
                timeout=PAGE_TIMEOUT,
            )
            changed = True
        except Exception:
            # The JS fingerprint above is intentionally only a wait aid.
            # Fall back to a fixed wait and verify the table afterward.
            pass

        self.page.wait_for_timeout(int(WAIT_AFTER_CLICK * 1000))

        new_fingerprint = self.table_fingerprint()

        if old_fingerprint and new_fingerprint:
            if new_fingerprint != old_fingerprint:
                changed = True

        if changed:
            return True

        # If the table could not be fingerprinted, allow one more
        # rendering period and compare the PDF/category URLs.
        self.page.wait_for_timeout(1000)

        final_fingerprint = self.table_fingerprint()

        if not old_fingerprint or final_fingerprint != old_fingerprint:
            return True

        print("[PAGINATION] Table did not change after Next.")
        return False

    # ----------------------------------------------------------
    # DOWNLOAD A SINGLE DISPLAYED PAGE
    # ----------------------------------------------------------

    def download_current_page_pdfs(self, folder: Path) -> int:
        pdfs = self.get_pdf_links()

        downloaded = 0

        for pdf in pdfs:
            if self.downloader.download(
                pdf["url"],
                folder,
                pdf["title"],
            ):
                downloaded += 1

        return downloaded

    # ----------------------------------------------------------
    # COLLECT ALL MAIN SECTION CONTENT
    # ----------------------------------------------------------

    def collect_main_section(
        self,
        section_url: str,
        section_name: str,
        root_folder: Path,
    ):
        """
        Walk every main pagination page.

        IMPORTANT DESIGN CHANGE:
        We collect all category URLs first, then process categories
        afterward. We do NOT navigate into a category while the main
        page is in the middle of pagination.

        This removes the old "return to main page and recreate page N"
        logic and prevents category/main-page state from getting mixed.
        """
        print()
        print("#" * 100)
        print(f"COLLECTING {section_name.upper()}")
        print("#" * 100)

        main_pdf_count = 0
        category_map = {}

        self.page.goto(
            section_url,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        self.page.wait_for_timeout(int(WAIT_AFTER_PAGE_LOAD * 1000))
        self.wait_for_document_table()

        page_number = 1
        visited_fingerprints = set()

        while True:
            print()
            print("-" * 100)
            print(f"{section_name} - MAIN PAGE {page_number}")
            print("-" * 100)

            fingerprint = self.table_fingerprint()

            # Prevent infinite pagination loops.
            if fingerprint and fingerprint in visited_fingerprints:
                print("[PAGINATION] Same table already seen. Stopping.")
                break

            if fingerprint:
                visited_fingerprints.add(fingerprint)

            # Direct PDFs on this exact page only.
            main_pdf_count += self.download_current_page_pdfs(root_folder)

            # Categories on this exact page only.
            categories = self.get_category_links(section_url)

            for category in categories:
                category_map[category["url"]] = category

            if not self.click_next_and_wait():
                break

            page_number += 1

        print()
        print(f"[MAIN COMPLETE] {section_name}")
        print(f"[MAIN PDF COUNT] {main_pdf_count}")
        print(f"[UNIQUE CATEGORIES] {len(category_map)}")

        return main_pdf_count, list(category_map.values())

    # ----------------------------------------------------------
    # PROCESS ONE CATEGORY
    # ----------------------------------------------------------

    def process_category(
        self,
        category_url: str,
        category_title: str,
        root_folder: Path,
    ) -> int:
        """
        Process every pagination page belonging to ONE category.

        Nothing from another category is collected here because all
        PDF selectors are scoped to the category's own [role="table"].
        """
        category_url = normalize_url(category_url)

        category_name = category_folder_name(
            category_title,
            category_url,
        )

        folder = root_folder / category_name
        folder.mkdir(parents=True, exist_ok=True)

        print()
        print("#" * 100)
        print("CATEGORY")
        print("#" * 100)
        print(f"[CATEGORY] {category_name}")
        print(f"[URL]      {category_url}")
        print(f"[FOLDER]   {folder}")

        total_downloaded = 0
        page_number = 1
        visited_fingerprints = set()

        try:
            self.page.goto(
                category_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT,
            )
            self.page.wait_for_timeout(int(WAIT_AFTER_PAGE_LOAD * 1000))
            self.wait_for_document_table()

            while True:
                print()
                print("-" * 100)
                print(f"{category_name} - CATEGORY PAGE {page_number}")
                print("-" * 100)

                fingerprint = self.table_fingerprint()

                # Never keep clicking a broken/stuck Next button.
                if fingerprint and fingerprint in visited_fingerprints:
                    print(
                        "[CATEGORY PAGINATION] "
                        "Same page detected. Stopping."
                    )
                    break

                if fingerprint:
                    visited_fingerprints.add(fingerprint)

                # ONLY this category's current table.
                total_downloaded += self.download_current_page_pdfs(folder)

                if not self.click_next_and_wait():
                    break

                page_number += 1

            print()
            print(f"[CATEGORY COMPLETE] {category_name}")
            print(f"[DOWNLOADED] {total_downloaded}")

            return total_downloaded

        except PlaywrightTimeoutError as exc:
            print(f"[CATEGORY TIMEOUT] {category_url}")
            print(f"[ERROR] {exc}")
            return total_downloaded

        except Exception as exc:
            print(f"[CATEGORY ERROR] {category_url}")
            print(f"[ERROR] {type(exc).__name__}: {exc}")
            return total_downloaded

    # ----------------------------------------------------------
    # PROCESS COMPLETE DOCUMENT SECTION
    # ----------------------------------------------------------

    def process_document_section(
        self,
        section_url: str,
        section_name: str,
    ) -> int:
        root_folder = DOWNLOAD_ROOT / clean_filename(section_name)
        root_folder.mkdir(parents=True, exist_ok=True)

        main_count, categories = self.collect_main_section(
            section_url,
            section_name,
            root_folder,
        )

        category_count = 0

        # Process each category on a clean navigation state.
        # Category URLs are unique because they came through category_map.
        for index, category in enumerate(categories, start=1):
            print()
            print(
                f"[CATEGORY {index}/{len(categories)}] "
                f"{category['title']}"
            )

            category_count += self.process_category(
                category["url"],
                category["title"],
                root_folder,
            )

        total = main_count + category_count

        print()
        print("=" * 100)
        print(f"{section_name.upper()} COMPLETE")
        print("=" * 100)
        print(f"Main-page PDFs : {main_count}")
        print(f"Category PDFs  : {category_count}")
        print(f"Section total  : {total}")

        return total

    # ----------------------------------------------------------
    # RUN
    # ----------------------------------------------------------

    def run(self):
        self.open_homepage()

        # Keep the requested UI flow.
        self.select_english()
        self.open_documents_menu()

        # Acts and Policies.
        acts_count = self.process_document_section(
            ACTS_URL,
            "Acts and Policies",
        )

        # Orders and Notices.
        orders_count = self.process_document_section(
            ORDERS_URL,
            "Orders and Notices",
        )

        print()
        print("=" * 100)
        print("SCRAPING FINISHED")
        print("=" * 100)
        print(f"Acts and Policies PDFs : {acts_count}")
        print(f"Orders and Notices PDFs : {orders_count}")
        print(f"TOTAL PDFs              : {acts_count + orders_count}")
        print(f"Download folder         : {DOWNLOAD_ROOT.resolve()}")
        print("=" * 100)


# ============================================================
# MAIN
# ============================================================

def main():
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)

    print("=" * 100)
    print("MINISTRY OF LABOUR DOCUMENT DOWNLOADER")
    print("=" * 100)
    print(f"Download directory: {DOWNLOAD_ROOT.resolve()}")
    print(f"Headless: {HEADLESS}")
    print()

    with sync_playwright() as p:
        # FIX:
        # Do NOT pass downloads=True to chromium.launch().
        # Downloads are enabled on the browser CONTEXT instead.
        browser = p.chromium.launch(
            headless=HEADLESS,
        )

        context = browser.new_context(
            accept_downloads=True,
            viewport={
                "width": 1440,
                "height": 900,
            },
            user_agent=USER_AGENT,
        )

        page = context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT)

        downloader = PDFDownloader(DOWNLOAD_ROOT)
        scraper = LabourMinistryScraper(page, downloader)

        try:
            scraper.run()

        except KeyboardInterrupt:
            print()
            print("[STOPPED] Scraper stopped by user.")

        except Exception as exc:
            print()
            print("=" * 100)
            print("FATAL ERROR")
            print("=" * 100)
            print(f"{type(exc).__name__}: {exc}")

        finally:
            print()
            print("[BROWSER] Closing browser...")

            try:
                context.close()
            except Exception:
                pass

            try:
                browser.close()
            except Exception:
                pass

            print("[DONE]")

    # Generate Excel report after browser closes
    export_to_excel()


if __name__ == "__main__":
    guarded_main(main)
