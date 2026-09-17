from pathlib import Path
from datetime import datetime
from html import unescape
from urllib.parse import urljoin, urlparse, unquote
import os
import re
import requests
import urllib3
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment

from playwright.sync_api import sync_playwright


# ============================================================
# CONFIGURATION
# ============================================================

START_URL = "https://parivesh.nic.in/#/dw-act-rule"

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))

DOWNLOAD_BASE = Path(os.environ.get("DOWNLOAD_BASE_PATH", SCRIPT_DIR))
DOWNLOAD_ROOT = DOWNLOAD_BASE / "PARIVESH"
AUDIT_XLSX = DOWNLOAD_ROOT / "PARIVESH_Audit.xlsx"

# The PARIVESH page shown in the reference image is a separate legal
# repository page.  Its table is populated from the API below and each
# "Click to View" link resolves through viewDocument?id=... to a PDF.
PARLIAMENT_NOTIFICATIONS_PAGE = "https://parivesh.nic.in/legal_repo/api/viewSubordinate"
PARLIAMENT_NOTIFICATIONS_API = "https://parivesh.nic.in/legal_repo/api/subordinateLegislation/getListPublished?type=PUBLISH"
PARLIAMENT_VIEW_DOCUMENT_URL = "https://parivesh.nic.in/legal_repo/api/viewDocument?id={}"
PARLIAMENT_NOTIFICATION_FOLDER = DOWNLOAD_ROOT / "Notifications_Laid_Before_Parliament"
PARLIAMENT_AUDIT_SHEET = "Parliament Notifications"

HEADLESS = False
TIMEOUT = 60000


# ============================================================
# SSL WARNING
# ============================================================

urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


# ============================================================
# PARIVESH CATEGORIES
# ============================================================

CATEGORIES = [
    (
        "Environmental_Acts_and_Rules",
        "#v-pills-home-tab",
        "#v-pills-home",
        [
            ("Acts", "#flush-collapseOne", "#flush-headingOne"),
            ("Rules", "#flush-collapseTwo", "#flush-headingTwo"),
            ("Regulations", "#flush-collapseFour", "#flush-headingFour"),
            ("Power Conferred by Environment Protection Act", "#flush-collapseThree", "#flush-headingThree", [
                ("Amendments to Principal Rules", "#EC-collapse2", "#EC-heading2"),
                ("Coastal Regulation Zone (CRZ)", "#EC-collapse3", "#EC-heading3"),
                ("Delegation of Powers", "#EC-collapse4", "#EC-heading4"),
                ("Eco-marks Scheme", "#EC-collapse5", "#EC-heading5"),
                ("Eco-sensitive Zone", "#EC-collapse6", "#EC-heading6"),
                ("Environmental Clearance - General", "#EC-collapse7", "#EC-heading7"),
                ("Environmental Impact Assessment Notification - 2006", "#EC-collapse8", "#EC-heading8"),
                ("Environmental Labs", "#EC-collapse9", "#EC-heading9"),
                ("Environmental Standards", "#EC-collapse10", "#EC-heading10"),
                ("Hazardous Substances Management", "#EC-collapse15", "#EC-heading15"),
                ("Loss of Ecology", "#EC-collapse11", "#EC-heading11"),
                ("Noise Pollution", "#EC-collapse16", "#EC-heading16"),
                ("Ozone Layer Depletion", "#EC-collapse12", "#EC-heading12"),
                ("Water Pollution", "#EC-collapse13", "#EC-heading13"),
            ]),
        ]
    ),
    (
        "Air_Acts_and_Rules",
        "#v-pills-air-tab",
        "#v-pills-air",
        [
            ("Acts", "#air-flush-collapseOne", "#air-flush-headingOne"),
            ("Rules", "#air-flush-collapseTwo", "#air-flush-headingTwo"),
            ("Regulations", "#air-flush-collapseThree", "#air-flush-headingThree"),
        ]
    ),
    (
        "Water_Acts_and_Rules",
        "#v-pills-water-tab",
        "#v-pills-water",
        [
            ("Acts", "#water-flush-collapseOne", "#water-flush-headingOne"),
            ("Rules", "#water-flush-collapseTwo", "#water-flush-headingTwo"),
            ("Regulations", "#water-flush-collapseThree", "#water-flush-headingThree"),
        ]
    ),
]


# ============================================================
# SAFE FILE NAME
# ============================================================

def safe_filename(name):

    name = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        name
    )

    name = re.sub(
        r"\s+",
        " ",
        name
    )

    return name.strip(" .")


# ============================================================
# GET PDF FILE NAME
# ============================================================

def get_filename(url):

    path = urlparse(url).path

    name = unquote(
        Path(path).name
    )

    if not name:
        name = "document.pdf"

    if not name.lower().endswith(".pdf"):
        name += ".pdf"

    return safe_filename(name)


# ============================================================
# CLICK CATEGORY
# ============================================================

def click_category(
    page,
    tab_selector,
    content_selector
):

    tab = page.locator(tab_selector)

    tab.wait_for(
        state="visible",
        timeout=TIMEOUT
    )

    tab.scroll_into_view_if_needed()

    try:
        tab.click(
            timeout=TIMEOUT
        )

    except Exception:

        page.evaluate(
            """
            selector => {
                const element =
                    document.querySelector(selector);

                if (element) {
                    element.click();
                }
            }
            """,
            tab_selector
        )

    page.wait_for_timeout(2000)

    content = page.locator(
        content_selector
    )

    content.wait_for(
        state="visible",
        timeout=TIMEOUT
    )

    page.wait_for_timeout(1500)

    return content


# ============================================================
# OPEN ACCORDIONS
# ============================================================

def open_accordions(
    page,
    content
):

    buttons = content.locator(
        "button.accordion-button"
    )

    count = buttons.count()

    for i in range(count):

        try:

            button = buttons.nth(i)

            if not button.is_visible():
                continue

            expanded = (
                button.get_attribute(
                    "aria-expanded"
                )
                or ""
            ).lower()

            if expanded != "true":

                button.click(
                    timeout=5000
                )

                page.wait_for_timeout(
                    300
                )

        except Exception:

            continue


# ============================================================
# FIND ALL PDF LINKS
# ============================================================

def get_pdf_links(content):

    pdf_links = []
    seen = set()

    anchors = content.locator(
        "a[href]"
    )

    count = anchors.count()

    for i in range(count):

        try:

            anchor = anchors.nth(i)
            href = anchor.get_attribute("href")

            if not href:
                continue

            href = href.strip()

            if href.lower().startswith(
                "javascript:"
            ):
                continue

            full_url = urljoin(
                "https://parivesh.nic.in",
                href
            )

            if ".pdf" not in full_url.lower():
                continue

            if full_url not in seen:
                row = anchor.locator("xpath=ancestor::tr")
                cells = row.locator("td")
                values = [cells.nth(j).inner_text().strip() for j in range(cells.count())]
                pdf_links.append({
                    "url": full_url,
                    "sn": values[0] if len(values) > 0 else "",
                    "title": values[1] if len(values) > 1 else anchor.inner_text().strip(),
                    "size": values[2] if len(values) > 2 else "",
                    "type": values[3] if len(values) > 3 else "PDF",
                })
                seen.add(full_url)

        except Exception:

            continue

    return pdf_links


# ============================================================
# DOWNLOAD ONE PDF
# ============================================================

def normalize_pdf_bytes(data):
    """Return PDF bytes when PARIVESH prepends an HTML wrapper."""

    marker = data.find(b"%PDF-")
    if marker < 0:
        return None

    if marker:
        print("[INFO] Removed PARIVESH HTML prefix before PDF bytes")
        data = data[marker:]

    eof = data.rfind(b"%%EOF")
    if eof >= 0:
        data = data[:eof + len(b"%%EOF")]

    return data


def download_pdf(
    session,
    url,
    folder,
    number
):

    try:

        response = session.get(
            url,
            timeout=90,
            verify=False,
            allow_redirects=True
        )

        if response.status_code != 200:

            print(
                f"[ERROR] HTTP "
                f"{response.status_code}"
            )

            return {"success": False, "status": "Failed", "local_file": "", "actual_size": "", "error": f"HTTP {response.status_code}"}

        data = normalize_pdf_bytes(response.content)

        if data is None:

            print(
                "[ERROR] Not a PDF"
            )

            return {"success": False, "status": "Failed", "local_file": "", "actual_size": "", "error": "Response was not a PDF"}

        filename = get_filename(
            response.url
        )

        output = (
            folder /
            f"{number:03d}_{filename}"
        )

        if (
            output.exists()
            and output.stat().st_size > 0
        ):

            print(
                f"[SKIP] {filename}"
            )

            return {"success": True, "status": "Skipped", "local_file": str(output), "actual_size": output.stat().st_size, "error": ""}

        output.write_bytes(
            data
        )

        print(
            f"[OK] {filename}"
        )

        return {"success": True, "status": "Downloaded", "local_file": str(output), "actual_size": output.stat().st_size, "error": ""}

    except Exception as e:

        print(
            f"[ERROR] Download failed:"
            f" {url}"
        )

        print(
            f"       {e}"
        )

        return {"success": False, "status": "Failed", "local_file": "", "actual_size": "", "error": str(e)}


# ============================================================
# PROCESS ONE CATEGORY
# ============================================================

def process_category(
    page,
    name,
    tab_selector,
    content_selector,
    sections
):

    print()
    print("=" * 60)
    print(
        f"CATEGORY: {name}"
    )
    print("=" * 60)

    try:

        content = click_category(
            page,
            tab_selector,
            content_selector
        )

        open_accordions(
            page,
            content
        )

        page.wait_for_timeout(
            1000
        )

        classified_links = []

        for section in sections:
            section_name, collapse_selector, heading_selector = section[:3]
            heading = content.locator(heading_selector + " button")
            if heading.is_visible() and (heading.get_attribute("aria-expanded") or "").lower() != "true":
                heading.click()
                page.wait_for_timeout(300)

            section_folder = DOWNLOAD_ROOT / name / section_name
            nested_sections = section[3] if len(section) > 3 else []

            if nested_sections:
                for nested_name, nested_collapse, nested_heading in nested_sections:
                    nested_button = content.locator(nested_heading + " button")
                    if nested_button.is_visible() and (nested_button.get_attribute("aria-expanded") or "").lower() != "true":
                        nested_button.click()
                        page.wait_for_timeout(200)
                    links = get_pdf_links(content.locator(nested_collapse))
                    for item in links:
                        classified_links.append((section_folder / nested_name, item["url"], item, section_name, nested_name))
            else:
                links = get_pdf_links(content.locator(collapse_selector))
                for item in links:
                    classified_links.append((section_folder, item["url"], item, section_name, ""))

        print(f"PDFs found: {len(classified_links)}")
        return classified_links

    except Exception as e:

        print(
            f"[ERROR] Could not read "
            f"{name}"
        )

        print(
            f"       {e}"
        )

        return []


# ============================================================
# MAIN
# ============================================================

def write_audit(rows):

    headers = [
        "Timestamp", "S.N.", "Category", "Classification", "Subcategory",
        "Title", "Size", "Type", "Source URL", "Local File",
        "Download Status", "Actual File Size (bytes)", "Error"
    ]

    if AUDIT_XLSX.exists():
        workbook = load_workbook(AUDIT_XLSX)
        sheet = workbook["Audit"] if "Audit" in workbook.sheetnames else workbook.create_sheet("Audit")
    else:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Audit"

    # Audit is a current-run snapshot. Clear stale rows so a previously
    # failed download does not remain after a later successful retry.
    if sheet.max_row:
        sheet.delete_rows(1, sheet.max_row)
    sheet.append(headers)

    for row in rows:
        sheet.append(row)

    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = [20, 8, 32, 24, 42, 70, 16, 10, 90, 70, 18, 22, 40]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index) if index <= 26 else "A"].width = width
    for cell in sheet["A"][1:]:
        cell.number_format = "yyyy-mm-dd hh:mm:ss"

    workbook.save(AUDIT_XLSX)
    print(f"Audit workbook saved: {AUDIT_XLSX.resolve()}")


# ============================================================
# NOTIFICATIONS LAID BEFORE THE PARLIAMENT
# ============================================================

def clean_parliament_value(value):
    """Keep the Excel cell text equivalent to the rendered site table."""

    value = re.sub(r"[\x00-\x1F\x7F]", " ", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def parliament_date(value):
    """Store site dates as sortable Excel dates while displaying dd-mm-yyyy."""

    value = clean_parliament_value(value)
    if not value or value.upper() in {"N/A", "NA", "NULL"}:
        return ""
    try:
        return datetime.strptime(value, "%d-%m-%Y")
    except ValueError:
        return value


def extract_parliament_pdf_url(html):
    """Extract the PDF URL placed in the viewDocument HTML wrapper."""

    patterns = (
        r'<input[^>]+id=["\']url_path["\'][^>]+value=["\']([^"\']+)',
        r'<input[^>]+value=["\']([^"\']+)["\'][^>]+id=["\']url_path["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return unescape(match.group(1)).strip()
    return ""


def parliament_get(session, url):
    """Retry transient PARIVESH API/DMS failures without an open-ended loop."""

    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, timeout=TIMEOUT, verify=False)
            if response.status_code >= 500 and attempt < 2:
                import time
                time.sleep(2 ** attempt)
                continue
            return response
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                import time
                time.sleep(2 ** attempt)
    if last_error:
        raise last_error
    raise RuntimeError(f"Request failed: {url}")


def parliament_notification_filename(row):
    """Create a stable, row-specific filename matching the site order."""

    counter = clean_parliament_value(row.get("counter")) or "0"
    gazette = clean_parliament_value(row.get("e_gazette_no")) or "No-Gazette"
    subject = clean_parliament_value(row.get("subject")) or "Unnamed-Notification"
    filename = safe_filename(f"{counter}_{gazette}_{subject}")
    return f"{filename[:180].rstrip(' .')}.pdf"


def download_parliament_notifications(session):
    """Download the exact rows shown on the Subordinate Legislation page."""

    PARLIAMENT_NOTIFICATION_FOLDER.mkdir(parents=True, exist_ok=True)
    rows = []

    try:
        response = parliament_get(session, PARLIAMENT_NOTIFICATIONS_API)
        response.raise_for_status()
        records = response.json()
    except Exception as exc:
        print(f"[ERROR] Parliament notification list failed: {exc}")
        return rows

    for record in records:
        row = {
            "counter": record.get("counter", ""),
            "e_gazette_no": clean_parliament_value(record.get("e_gazette_no")),
            "issue_date": parliament_date(record.get("issue_date")),
            "subject": clean_parliament_value(record.get("subject")),
            "laying_date_lok_sabha": parliament_date(record.get("laying_date_lok_sabha")),
            "laying_date_rajya_sabha": parliament_date(record.get("laying_date_rajya_sabha")),
            "pdf_path": "",
        }

        document_id = clean_parliament_value(record.get("notification_doc"))
        if not document_id:
            rows.append(row)
            continue

        output = PARLIAMENT_NOTIFICATION_FOLDER / parliament_notification_filename(row)

        try:
            if output.exists() and output.stat().st_size > 0:
                row["pdf_path"] = str(output.resolve())
                rows.append(row)
                continue

            view_url = PARLIAMENT_VIEW_DOCUMENT_URL.format(document_id)
            view_response = parliament_get(session, view_url)
            view_response.raise_for_status()

            if view_response.content.startswith(b"%PDF"):
                pdf_url = view_url
            else:
                pdf_url = extract_parliament_pdf_url(view_response.text)

            if not pdf_url:
                raise RuntimeError("PDF URL was not present in viewDocument response")

            if output.exists() and output.stat().st_size > 0:
                row["pdf_path"] = str(output.resolve())
                continue

            pdf_response = parliament_get(session, pdf_url)
            pdf_response.raise_for_status()
            data = normalize_pdf_bytes(pdf_response.content)
            if data is None:
                raise RuntimeError("document response was not a PDF")

            output.write_bytes(data)
            row["pdf_path"] = str(output.resolve())
        except Exception as exc:
            print(f"[ERROR] Parliament notification {document_id}: {exc}")

        rows.append(row)

    return rows


def write_parliament_notification_audit(rows):
    """Write the screenshot table, replacing Document/View with PDF File Path."""

    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    if AUDIT_XLSX.exists():
        workbook = load_workbook(AUDIT_XLSX)
    else:
        workbook = Workbook()

    if PARLIAMENT_AUDIT_SHEET in workbook.sheetnames:
        del workbook[PARLIAMENT_AUDIT_SHEET]

    if "Sheet" in workbook.sheetnames:
        sheet_default = workbook["Sheet"]
        if sheet_default.max_row == 1 and sheet_default.max_column == 1 and sheet_default["A1"].value is None:
            del workbook["Sheet"]

    sheet = workbook.create_sheet(PARLIAMENT_AUDIT_SHEET)
    headers = [
        "Sr.No.",
        "E-Gazette No.",
        "Date of Notification",
        "Subject",
        "Date of Laying Before Lok Sabha",
        "Date of Laying Before Rajya Sabha",
        "PDF File Path",
    ]
    sheet.append(headers)

    for row in rows:
        sheet.append([
            row["counter"],
            row["e_gazette_no"],
            row["issue_date"],
            row["subject"],
            row["laying_date_lok_sabha"],
            row["laying_date_rajya_sabha"],
            row["pdf_path"],
        ])

    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=False)
        for index in (3, 5, 6):
            row[index - 1].number_format = "dd-mm-yyyy"

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = {
        "A": 10,
        "B": 18,
        "C": 20,
        "D": 85,
        "E": 28,
        "F": 30,
        "G": 120,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    sheet.sheet_view.showGridLines = False

    workbook.save(AUDIT_XLSX)
    print(f"Parliament notification audit saved: {AUDIT_XLSX.resolve()} ({len(rows)} rows)")

def main():

    DOWNLOAD_ROOT.mkdir(
        parents=True,
        exist_ok=True
    )

    all_categories = []

    # --------------------------------------------------------
    # OPEN WEBSITE AND COLLECT PDF LINKS
    # --------------------------------------------------------

    with sync_playwright() as p:

        print()
        print("=" * 60)
        print("STARTING PARIVESH AUTOMATION")
        print("=" * 60)

        browser = p.chromium.launch(
            headless=HEADLESS
        )

        context = browser.new_context(
            ignore_https_errors=True
        )

        page = context.new_page()

        page.set_default_timeout(
            TIMEOUT
        )

        print(
            "\nOpening PARIVESH..."
        )

        page.goto(
            START_URL,
            wait_until="domcontentloaded",
            timeout=TIMEOUT
        )

        print(
            "Waiting for website..."
        )

        page.wait_for_selector(
            "#v-pills-air-tab",
            timeout=TIMEOUT
        )

        page.wait_for_timeout(
            5000
        )

        print(
            "PARIVESH loaded successfully."
        )

        # ----------------------------------------------------
        # CHECK ALL 9 CATEGORIES
        # ----------------------------------------------------

        for (
            name,
            tab_selector,
            content_selector,
            sections
        ) in CATEGORIES:

            links = process_category(
                page,
                name,
                tab_selector,
                content_selector,
                sections
            )

            all_categories.append(
                (name, links)
            )

        # ----------------------------------------------------
        # CLOSE BROWSER
        # ----------------------------------------------------

        browser.close()

    # ========================================================
    # DOWNLOAD ALL PDFs
    # ========================================================

    print()
    print("=" * 60)
    print("STARTING PDF DOWNLOAD")
    print("=" * 60)

    session = requests.Session()

    session.verify = False

    session.headers.update(
        {
            "User-Agent":
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/131.0.0.0 "
                "Safari/537.36",

            "Accept":
                "application/pdf,"
                "application/octet-stream,"
                "*/*",

            "Referer":
                "https://parivesh.nic.in/"
        }
    )

    total_found = 0
    total_downloaded = 0
    audit_rows = []
    audit_timestamp = datetime.now()

    # --------------------------------------------------------
    # EACH CATEGORY
    # --------------------------------------------------------

    for name, links in all_categories:

        print()
        print("-" * 60)
        print(
            f"DOWNLOADING: {name}"
        )
        print("-" * 60)

        total_found += len(links)

        counters = {}

        for folder, url, item, classification, subcategory in links:
            counters[folder] = counters.get(folder, 0) + 1
            folder.mkdir(parents=True, exist_ok=True)

            result = download_pdf(
                session,
                url,
                folder,
                counters[folder]
            )

            if result["success"]:
                total_downloaded += 1

            audit_rows.append([
                audit_timestamp,
                item["sn"],
                name,
                classification,
                subcategory,
                item["title"],
                item["size"],
                item["type"],
                url,
                result["local_file"],
                result["status"],
                result["actual_size"],
                result["error"],
            ])

    # --------------------------------------------------------
    # NOTIFICATIONS LAID BEFORE THE PARLIAMENT
    # --------------------------------------------------------

    print()
    print("STARTING PARLIAMENT NOTIFICATION DOWNLOAD")
    parliament_rows = download_parliament_notifications(session)
    write_parliament_notification_audit(parliament_rows)
    parliament_downloaded = sum(1 for row in parliament_rows if row["pdf_path"])
    print(
        f"Parliament notification records: {len(parliament_rows)}; "
        f"PDF paths available: {parliament_downloaded}"
    )

    session.close()
    write_audit(audit_rows)

    # ========================================================
    # FINAL RESULT
    # ========================================================

    print()
    print("=" * 60)
    print("PARIVESH DOWNLOAD COMPLETED")
    print("=" * 60)

    print(
        f"Total PDF links found : "
        f"{total_found}"
    )

    print(
        f"Total PDFs downloaded  : "
        f"{total_downloaded}"
    )

    print()
    print(
        "PDFs saved here:"
    )

    print(
        DOWNLOAD_ROOT.resolve()
    )

    print()
    print("=" * 60)
    print("DONE")
    print("=" * 60)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
