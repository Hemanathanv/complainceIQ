
from playwright.sync_api import sync_playwright
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin, unquote, urlparse, parse_qs
import os
import json
import random
import re
import time
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment


# ==========================================================
# FOLDERS
# ==========================================================

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = next(parent / ".env" for parent in Path(__file__).resolve().parents if (parent / "docker-compose.yml").is_file())
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))

DOWNLOAD_BASE = Path(os.environ.get("DOWNLOAD_BASE_PATH", SCRIPT_DIR))
# The source filename is longer than the Windows path limit once category
# folders and PDF filenames are appended, so use a stable compact parent.
BASE = DOWNLOAD_BASE / "008 009 010 013 014 020 021 023 Labour Acts"
RUNTIME_DIR = BASE / "logs"
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


def safe_goto(page, url, **kwargs):
    time.sleep(REQUEST_DELAY_SECONDS + random.uniform(0, 0.35))
    response = page.goto(url, **kwargs)
    if response is not None:
        check_response_status(response.status, url)
    return response


def guarded_request_get(context, url, **kwargs):
    time.sleep(REQUEST_DELAY_SECONDS + random.uniform(0, 0.35))
    response = context.request.get(url, **kwargs)
    check_response_status(response.status, url)
    return response


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

CIRCULAR_FOLDER = BASE / "Circulars"
ACTS_FOLDER = BASE / "Acts"
AUDIT_XLSX = BASE / "CLC_Audit.xlsx"

CIRCULAR_FOLDER.mkdir(parents=True, exist_ok=True)
ACTS_FOLDER.mkdir(parents=True, exist_ok=True)


# ==========================================================
# URLS
# ==========================================================

CLC_HOME = "https://clc.gov.in/"

CIRCULAR_URL = "https://clc.gov.in/clc/circulars"

ACTS_URL = "https://clc.gov.in/clc/acts-rules/acts-and-rules-0"


# ==========================================================
# 15 ACTS - EXACT WEBSITE ORDER
# ==========================================================

ACTS = [
    "The Contract Labour (Regulation & Abolition)Act,and Rules, 1970",
    "The Building and Other Construction Works (RE & CE) Act, 1996",
    "The Minimum Wages Act, 1948",
    "The Equal Remuneration Act, 1976",
    "The Payment of Bonus Act, and Rules 1976",
    "The Interstate Migrant Workmen (RE &CS) Act and Rules, 1979",
    "The Maternity Benefit Act, 1961",
    "The Payment of Gratuity Act, 1972",
]

def act_folder(title):
    if "Payment of Bonus" in title or "Payment of Gratuity" in title:
        return ACTS_FOLDER / "Payment of Bonus and Gratuity Acts"
    mapping = {
        "Contract Labour": "Contract Labour Act and Rules 1970",
        "Building and Other Construction": "BOCW Act 1996",
        "Minimum Wages": "Minimum Wages Act",
        "Equal Remuneration": "Equal Remuneration Act 1976",
        "Interstate Migrant": "Inter-State Migrant Workmen Act",
        "Maternity Benefit": "Maternity Benefit Act 1961",
    }
    for key, folder in mapping.items():
        if key in title:
            return ACTS_FOLDER / folder
    return ACTS_FOLDER / clean_name(title)


# ==========================================================
# CLEAN FILE NAME
# ==========================================================

def clean_name(name):
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name.strip()


def parse_clc_date(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return value


# ==========================================================
# FIND PDF ON PAGE
# ==========================================================

def find_pdf(page):

    # Direct PDF links
    links = page.locator("a[href]")

    for i in range(links.count()):

        href = links.nth(i).get_attribute("href")

        if href:

            url = urljoin(page.url, href)

            if ".pdf" in url.lower():
                return url

    # IFRAME
    frames = page.locator("iframe")

    for i in range(frames.count()):

        src = frames.nth(i).get_attribute("src")

        if not src:
            continue

        src = urljoin(page.url, src)

        if ".pdf" in src.lower():
            return src

        match = re.search(
            r'[?&]file=([^&]+)',
            src,
            re.I
        )

        if match:

            pdf = unquote(match.group(1))
            pdf = urljoin(page.url, pdf)

            if ".pdf" in pdf.lower():
                return pdf

    # EMBED
    embeds = page.locator("embed")

    for i in range(embeds.count()):

        src = embeds.nth(i).get_attribute("src")

        if src and ".pdf" in src.lower():
            return urljoin(page.url, src)

    # OBJECT
    objects = page.locator("object")

    for i in range(objects.count()):

        src = objects.nth(i).get_attribute("data")

        if src and ".pdf" in src.lower():
            return urljoin(page.url, src)

    return None


# ==========================================================
# DOWNLOAD PDF WITHOUT OPENING
# USED ONLY FOR CIRCULARS
# ==========================================================

def save_pdf_silently(context, pdf_url, folder, title):

    filename = folder / (
        clean_name(title) + ".pdf"
    )

    if filename.exists():

        print("SKIP:", filename.name)
        return filename

    try:

        response = guarded_request_get(context,
            pdf_url,
            timeout=60000
        )

        if not response.ok:

            print("DOWNLOAD FAILED:", title)
            return

        data = response.body()

        if not data.startswith(b"%PDF"):

            print("NOT A PDF:", title)
            return

        filename.write_bytes(data)

        print("SAVED:", filename.name)
        return filename

    except Exception as e:

        print("PDF ERROR:", title)
        print(e)
        return None


# ==========================================================
# CIRCULARS / ORDERS
#
# ONLY PAGE 1 + PAGE 2 ARE OPENED
# PDF IS NEVER OPENED
# ==========================================================

def circulars(context):

    print()
    print("=" * 60)
    print("CIRCULARS / ORDERS")
    print("=" * 60)

    circular_dates = {}

    for page_number in [0, 1]:

        url = (
            CIRCULAR_URL
            + f"?page={page_number}"
        )

        print()
        print(
            f"OPENING CIRCULAR PAGE {page_number + 1}"
        )

        page = context.new_page()

        try:

            safe_goto(page,
                url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(2500)

            print(
                f"CIRCULAR PAGE {page_number + 1} OPENED"
            )

            rows = page.locator(
                "table.views-table tbody tr"
            )

            print(
                "ITEMS:",
                rows.count()
            )

            # Download PDFs silently
            for i in range(rows.count()):

                row = rows.nth(i)

                title_locator = row.locator(
                    ".views-field-title"
                )

                if not title_locator.count():
                    continue

                title = (
                    title_locator
                    .inner_text()
                    .strip()
                )

                cells = row.locator("td")
                date = ""
                if cells.count() >= 4:
                    date = parse_clc_date(cells.nth(3).inner_text())

                link = row.locator(
                    "a[href]"
                ).first

                if not link.count():
                    continue

                href = link.get_attribute("href")

                if not href:
                    continue

                url2 = urljoin(
                    page.url,
                    href
                )

                # Direct PDF
                if ".pdf" in url2.lower():

                    saved_file = save_pdf_silently(
                        context,
                        url2,
                        CIRCULAR_FOLDER,
                        title
                    )
                    if saved_file:
                        circular_dates[str(saved_file.resolve())] = date

                    continue

                # Get node page WITHOUT opening it
                try:

                    response = guarded_request_get(context,
                        url2,
                        timeout=60000
                    )

                    if not response.ok:
                        continue

                    html = response.text()

                    pdf_url = find_pdf_from_html(
                        html,
                        url2
                    )

                    if pdf_url and "viewer.html" in pdf_url.lower():
                        embedded = parse_qs(urlparse(pdf_url).query).get("file", [None])[0]
                        pdf_url = unquote(embedded) if embedded else None

                    if pdf_url:

                        saved_file = save_pdf_silently(
                            context,
                            pdf_url,
                            CIRCULAR_FOLDER,
                            title
                        )
                        if saved_file:
                            circular_dates[str(saved_file.resolve())] = date
                    else:
                        # CLC circular pages embed the PDF in PDF.js.
                        doc_page = context.new_page()
                        try:
                            safe_goto(doc_page, url2, wait_until="domcontentloaded", timeout=60000)
                            doc_page.wait_for_timeout(1200)
                            viewer = doc_page.locator("iframe[src*='pdf.js']").first
                            viewer_src = viewer.get_attribute("src") if viewer.count() else None
                            if viewer_src:
                                embedded = parse_qs(urlparse(viewer_src).query).get("file", [None])[0]
                                if embedded:
                                    saved_file = save_pdf_silently(context, unquote(embedded), CIRCULAR_FOLDER, title)
                                    if saved_file:
                                        circular_dates[str(saved_file.resolve())] = date
                        except Exception as e:
                            print("Circular viewer error:", title)
                            print(e)
                        finally:
                            doc_page.close()

                except Exception as e:

                    print(
                        "Circular error:",
                        title
                    )

                    print(e)

        except Exception as e:

            print("Circular page error:")
            print(e)

    return circular_dates


# ==========================================================
# FIND PDF URL FROM HTML
# ==========================================================

def find_pdf_from_html(html, base_url):

    # href
    matches = re.findall(
        r'''href\s*=\s*["']([^"']+)["']''',
        html,
        re.I
    )

    for value in matches:

        url = urljoin(
            base_url,
            value
        )

        if ".pdf" in url.lower():
            return url

    # src
    matches = re.findall(
        r'''(?:src|data)\s*=\s*["']([^"']+)["']''',
        html,
        re.I
    )

    for value in matches:

        url = urljoin(
            base_url,
            value
        )

        if ".pdf" in url.lower():
            return url

    # PDF URL anywhere
    match = re.search(
        r'''https?://[^"'<> ]+\.pdf[^"'<> ]*''',
        html,
        re.I
    )

    if match:
        return match.group(0)

    # Relative PDF
    match = re.search(
        r'''["']([^"']+\.pdf[^"']*)["']''',
        html,
        re.I
    )

    if match:

        return urljoin(
            base_url,
            match.group(1)
        )

    return None


# ==========================================================
# OPEN ACT PDF + SAVE
#
# ACT PDF ONLY
# ==========================================================

def open_act_pdf(
    context,
    page,
    pdf_url,
    title
):

    folder = act_folder(title)
    folder.mkdir(parents=True, exist_ok=True)
    filename = folder / (
        clean_name(title) + ".pdf"
    )

    if filename.exists():

        print(
            "ALREADY EXISTS:",
            filename.name
        )

        return

    print()
    print("OPENING ACT PDF:")
    print(pdf_url)

    try:

        # Open PDF in Chrome PDF Viewer
        safe_goto(page,
            pdf_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(4000)

        print(
            "PDF OPENED IN CHROME"
        )

        # Save actual PDF
        response = guarded_request_get(context,
            pdf_url,
            timeout=60000
        )

        if response.ok:

            data = response.body()

            if data.startswith(b"%PDF"):

                filename.write_bytes(data)

                print(
                    "SAVED:",
                    filename.name
                )

            else:

                print(
                    "NOT A PDF:",
                    title
                )

        else:

            print(
                "PDF SAVE FAILED:",
                title
            )

    except Exception as e:

        print(
            "ACT PDF ERROR:",
            title
        )

        print(e)


# ==========================================================
# CREATE PDF FROM ACT CONTENT
# ==========================================================

def create_act_pdf(
    context,
    url,
    title
):

    folder = act_folder(title)
    folder.mkdir(parents=True, exist_ok=True)
    filename = folder / (
        clean_name(title) + ".pdf"
    )

    if filename.exists():

        print(
            "ALREADY EXISTS:",
            filename.name
        )

        return

    page = context.new_page()

    try:

        print()
        print(
            "NO PDF FOUND"
        )

        print(
            "CREATING PDF FROM ACT CONTENT"
        )

        safe_goto(page,
            url,
            wait_until="networkidle",
            timeout=60000
        )

        page.wait_for_timeout(
            2500
        )

        # Click Read More if available
        read_more = page.locator(
            "a",
            has_text=re.compile(
                r"read\s*more",
                re.I
            )
        ).first

        if read_more.count():

            print(
                "READ MORE FOUND"
            )

            try:

                read_more.click(
                    timeout=10000
                )

                page.wait_for_timeout(
                    2500
                )

            except:

                pass

        # Keep main content
        page.evaluate("""
        () => {

            const content =
                document.querySelector('#post-content') ||
                document.querySelector('main');

            if (content) {

                document.body.innerHTML =
                    content.outerHTML;
            }

            document.querySelectorAll(
                'script, style, nav, footer, header'
            ).forEach(
                e => e.remove()
            );
        }
        """)

        page.wait_for_timeout(1000)

        # Create PDF
        page.pdf(
            path=str(filename),
            format="A4",
            print_background=True,
            display_header_footer=False,
            margin={
                "top": "25px",
                "bottom": "25px",
                "left": "25px",
                "right": "25px"
            }
        )

        print(
            "CREATED:",
            filename.name
        )

    except Exception as e:

        print(
            "CREATE PDF ERROR:",
            title
        )

        print(e)

    finally:

        page.close()


# ==========================================================
# ACTS AND RULES
# ==========================================================

def acts(context):

    print()
    print("=" * 60)
    print("ACTS -> ACTS AND RULES")
    print("=" * 60)

    acts_page = context.new_page()

    try:

        safe_goto(acts_page,
            ACTS_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        acts_page.wait_for_timeout(
            3000
        )

        print(
            "ACTS AND RULES PAGE OPENED"
        )

        rows = acts_page.locator(
            "#post-content .node-acts-rules "
            "table tbody tr"
        )

        print(
            "ACT ROWS FOUND:",
            rows.count()
        )

        # ==================================================
        # PROCESS ONLY THE IN-SCOPE ACTS
        # ==================================================

        for number in range(len(ACTS)):

            act_name = ACTS[number]

            print()
            print("=" * 60)
            print(
                f"ACT {number + 1}/{len(ACTS)}"
            )
            print(act_name)
            print("=" * 60)

            # Find exact act link
            link = acts_page.locator(
                "#post-content .node-acts-rules "
                "table tbody tr a",
                has_text=re.compile(
                    re.escape(act_name),
                    re.I
                )
            ).first

            # Fallback by row number
            if (
                not link.count()
                and number < rows.count()
            ):

                link = rows.nth(
                    number
                ).locator(
                    "a"
                ).first

            if not link.count():

                print(
                    "ACT LINK NOT FOUND"
                )

                continue

            href = link.get_attribute(
                "href"
            )

            if not href:

                print(
                    "HREF NOT FOUND"
                )

                continue

            act_url = urljoin(
                ACTS_URL,
                href
            )

            print(
                "ACT URL:",
                act_url
            )

            # ==================================================
            # DIRECT PDF
            # ==================================================

            if ".pdf" in act_url.lower():

                act_pdf_page = context.new_page()

                open_act_pdf(
                    context,
                    act_pdf_page,
                    act_url,
                    act_name
                )

                # Keep only necessary page
                act_pdf_page.close()

                continue

            # ==================================================
            # ACT PAGE
            # ==================================================

            act_page = context.new_page()

            try:

                safe_goto(act_page,
                    act_url,
                    wait_until="domcontentloaded",
                    timeout=60000
                )

                act_page.wait_for_timeout(
                    2500
                )

                print(
                    "ACT PAGE OPENED"
                )

                # --------------------------------------------------
                # Find existing PDF
                # --------------------------------------------------

                pdf_url = find_pdf(
                    act_page
                )

                # --------------------------------------------------
                # Read More
                # --------------------------------------------------

                if not pdf_url:

                    read_more = act_page.locator(
                        "a",
                        has_text=re.compile(
                            r"read\s*more",
                            re.I
                        )
                    ).first

                    if read_more.count():

                        print(
                            "READ MORE -> CLICK"
                        )

                        try:

                            read_more.click(
                                timeout=10000
                            )

                            act_page.wait_for_timeout(
                                3000
                            )

                        except:

                            pass

                        pdf_url = find_pdf(
                            act_page
                        )

                # --------------------------------------------------
                # PDF EXISTS
                # --------------------------------------------------

                if pdf_url:

                    print(
                        "PDF FOUND"
                    )

                    open_act_pdf(
                        context,
                        act_page,
                        pdf_url,
                        act_name
                    )

                # --------------------------------------------------
                # NO PDF
                # --------------------------------------------------

                else:

                    print(
                        "NO PDF FOUND"
                    )

                    create_act_pdf(
                        context,
                        act_url,
                        act_name
                    )

            except Exception as e:

                print(
                    "ACT ERROR:",
                    act_name
                )

                print(e)

            finally:

                act_page.close()

    except Exception as e:

        print(
            "ACTS ERROR:"
        )

        print(e)

    finally:

        acts_page.close()


# ==========================================================
# MAIN
# ==========================================================

def write_audit(circular_dates=None):
    circular_dates = circular_dates or {}
    headers = [
        "Timestamp", "S.N.", "Category", "Classification", "Subcategory",
        "Published Date", "Title", "Size", "Type", "Source URL", "Local File",
        "Download Status", "Actual File Size (bytes)", "Error"
    ]
    if AUDIT_XLSX.exists():
        workbook = load_workbook(AUDIT_XLSX)
        sheet = workbook["Audit"] if "Audit" in workbook.sheetnames else workbook.create_sheet("Audit")
    else:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Audit"

    # This sheet is a current-run snapshot. Remove stale rows so a previous
    # audit without dates does not remain mixed with the new dated rows.
    if sheet.max_row:
        sheet.delete_rows(1, sheet.max_row)
    sheet.append(headers)
    timestamp = datetime.now()
    sn = 0
    for path in sorted(ACTS_FOLDER.rglob("*.pdf")):
        sn += 1
        sheet.append([timestamp, sn, "CLC", "Acts", path.parent.name, "", path.stem, "N/A", "PDF", "", str(path), "Downloaded", path.stat().st_size, ""])
    for path in sorted(CIRCULAR_FOLDER.glob("*.pdf")):
        sn += 1
        published_date = circular_dates.get(str(path.resolve()), "")
        sheet.append([timestamp, sn, "CLC", "Circulars", "", published_date, path.stem, "N/A", "PDF", CIRCULAR_URL, str(path), "Downloaded", path.stat().st_size, ""])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for col, width in enumerate([20, 8, 12, 18, 42, 18, 70, 16, 10, 80, 80, 18, 22, 40], 1):
        sheet.column_dimensions[chr(64 + col)].width = width
    for cell in sheet["A"][1:]:
        cell.number_format = "yyyy-mm-dd hh:mm:ss"
    for cell in sheet["F"][1:]:
        cell.number_format = "dd/mm/yyyy"
    workbook.save(AUDIT_XLSX)
    print("Audit workbook:", AUDIT_XLSX)

def main():

    with sync_playwright() as p:

        print()
        print("=" * 60)
        print("STARTING CLC AUTOMATION")
        print("=" * 60)

        # ==================================================
        # ONLY ONE CHROME
        # ==================================================

        browser = p.chromium.launch(
            headless=False,
            channel="chrome"
        )

        context = browser.new_context(
            accept_downloads=True
        )

        # ==================================================
        # OPEN CLC
        # ==================================================

        home = context.new_page()

        safe_goto(home,
            CLC_HOME,
            wait_until="domcontentloaded",
            timeout=60000
        )

        home.wait_for_timeout(
            3000
        )

        print(
            "CLC WEBSITE OPENED"
        )

        # ==================================================
        # 1. CIRCULARS
        # ==================================================

        circular_dates = circulars(
            context
        )

        # ==================================================
        # 2. ACTS
        # ==================================================

        acts(
            context
        )

        write_audit(circular_dates)

        # ==================================================
        # COMPLETED
        # ==================================================

        print()
        print("=" * 60)
        print("AUTOMATION COMPLETED")
        print("=" * 60)

        print()
        print(
            "CLC Circular files:"
        )
        print(
            CIRCULAR_FOLDER
        )

        print()
        print(
            "CLC Act files:"
        )
        print(
            ACTS_FOLDER
        )

        print()
        print(
            "Circular Page 1 + Page 2 are open."
        )

        print(
            "Circular PDFs were NOT opened."
        )

        print(
            "Acts PDFs were opened and saved."
        )

        print(
            "Acts without PDFs were converted to PDFs."
        )

        print()
        print("Closing Chrome automatically.")
        context.close()
        browser.close()


# ==========================================================
# RUN
# ==========================================================

if __name__ == "__main__":
    guarded_main(main)
