"""
Income Tax Act document downloader
==================================

Run from PowerShell:
    python "003 Income Tax Act.py"

Downloads are stored under:
    IncomeTax/Acts/
    IncomeTax/Act_2026/Sections/
    IncomeTax/Act_2025/Sections/
    IncomeTax/Circulars/
    IncomeTax/Notifications/

The default setting processes all available records. To limit a test run:
    $env:INCOME_TAX_MAX_ITEMS = "10"
"""

import os
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from playwright.sync_api import sync_playwright


SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))

BASE = Path(os.environ.get("DOWNLOAD_BASE_PATH", SCRIPT_DIR)) / Path(__file__).stem
RUNTIME_DIR = BASE / "logs"
RUN_STATUS = RUNTIME_DIR / "run_status.json"
RUNTIME_LOG = RUNTIME_DIR / "runtime.log"
RESILIENCE_ATTEMPTS = int(os.environ.get("SCRAPER_MAX_ATTEMPTS", "3"))
REQUEST_DELAY = float(os.environ.get("SCRAPER_REQUEST_DELAY_SECONDS", "1.0"))
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
    time.sleep(REQUEST_DELAY + random.uniform(0, 0.35))
    response = page.goto(url, **kwargs)
    if response is not None:
        check_response_status(response.status, url)
    return response


def write_run_status(state, message="", attempt=0):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"script": Path(__file__).name, "state": state, "attempt": attempt,
               "message": str(message), "timestamp": datetime.now().astimezone().isoformat(timespec="seconds")}
    temporary = RUN_STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(RUN_STATUS)
    with RUNTIME_LOG.open("a", encoding="utf-8") as stream:
        stream.write(f"{payload['timestamp']} | {state} | attempt={attempt} | {message}\n")


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
ACT_FOLDER = BASE / "Acts"
CIRCULAR_FOLDER = BASE / "Circulars"
NOTIFICATION_FOLDER = BASE / "Notifications"
AUDIT_FILE = BASE / "IncomeTax_Audit.xlsx"

ACT_URL = "https://www.incometaxindia.gov.in/income-tax-act-2025"
HOME_URL = "https://www.incometaxindia.gov.in/home"
SOURCES = {
    "Circular": "https://www.incometaxindia.gov.in/circulars",
    "Notification": "https://www.incometaxindia.gov.in/notifications",
}
MAX_ITEMS = int(os.environ.get("INCOME_TAX_MAX_ITEMS", "0"))  # 0 = full run
HEADLESS = os.environ.get("INCOME_TAX_HEADLESS", "false").lower() in {"1", "true", "yes"}
TIMEOUT = 60000
ACT_YEARS = ("2026", "2025")


def safe_filename(value):
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r'[<>:"/\\|?*]', "_", value)
    return (value[:170] or "IncomeTax_document")


def unique_path(folder, filename):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    n = 1
    while path.exists():
        path = folder / f"{Path(filename).stem}_{n}{Path(filename).suffix}"
        n += 1
    return path


def close_temporary_tabs(context, keep):
    """Close every popup/PDF tab opened for a record, preserving the listing tab."""
    for opened in list(context.pages):
        if opened != keep:
            try:
                opened.close()
            except Exception:
                pass


def visible_next_button(page):
    """Find the site's next-page control across desktop/mobile templates."""
    selectors = (
        "#pagination-next-button",
        "button[aria-label*='next' i]",
        "a[aria-label*='next' i]",
        "button:has-text('Next')",
        "a:has-text('Next')",
        "button:has-text('›')",
        "a:has-text('›')",
        "button:has-text('»')",
        "a:has-text('»')",
    )
    for selector in selectors:
        for candidate in page.locator(selector).all():
            try:
                if candidate.is_visible() and not candidate.is_disabled():
                    return candidate
            except Exception:
                continue
    return None


def extract_records(page, category):
    return page.evaluate("""category => {
      const prefix = category === 'Circular' ? /^Circular No/i : /^Notification No/i;
      const dateRe = /Published On\\s*:\\s*([^\\n]+)/i;
      const records = [];
      for (const link of [...document.querySelectorAll('a')].filter(a => prefix.test(a.innerText.trim()))) {
        let node = link, block = '';
        for (let i = 0; node && i < 8; i++, node = node.parentElement) {
          const text = (node.innerText || '').trim();
          if (dateRe.test(text) && text.length < 6000) { block = text; break; }
        }
        const match = block.match(dateRe);
        records.push({name: link.innerText.trim(), date: match ? match[1].trim() : '', href: link.href, category});
      }
      return records;
    }""", category)


def download_from_detail(context, record, folder, number):
    detail = context.new_page()
    try:
        safe_goto(detail, record["href"], wait_until="domcontentloaded", timeout=TIMEOUT)
        detail.wait_for_timeout(1200)
        pdf_links = detail.locator("a[href*='.pdf'], a[href*='.PDF']")
        if pdf_links.count():
            href = pdf_links.first.get_attribute("href")
            url = urljoin(detail.url, href)
            response = context.request.get(url, timeout=TIMEOUT)
            data = response.body()
            if response.ok and data.startswith(b"%PDF"):
                path = unique_path(folder, f"{number:04d}_{safe_filename(record['name'])}.pdf")
                path.write_bytes(data)
                return str(path.resolve())
        buttons = detail.locator("button").filter(has_text=re.compile("Download PDF|Print", re.I))
        if buttons.count():
            try:
                with detail.expect_download(timeout=15000) as info:
                    buttons.first.click()
                path = unique_path(folder, f"{number:04d}_{safe_filename(record['name'])}.pdf")
                info.value.save_as(path)
                return str(path.resolve())
            except Exception:
                pass
        return "PDF NOT FOUND"
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"
    finally:
        detail.close()


def download_from_card(context, page, trigger, folder, number, name):
    """Use the original site's card-button workflow: direct href, download event, then new tab."""
    folder.mkdir(parents=True, exist_ok=True)
    existing = sorted(folder.glob(f"{number:04d}_*.pdf"))
    if existing:
        return str(existing[0].resolve())
    target = unique_path(folder, f"{number:04d}_{safe_filename(name)}.pdf")
    try:
        href = trigger.get_attribute("href")
        if href and ".pdf" in href.lower():
            response = context.request.get(urljoin(page.url, href), timeout=TIMEOUT)
            data = response.body()
            if response.ok and data.startswith(b"%PDF"):
                target.write_bytes(data)
                return str(target.resolve())
    except Exception:
        close_temporary_tabs(context, page)
    try:
        with page.expect_download(timeout=15000) as info:
            trigger.click()
        info.value.save_as(target)
        return str(target.resolve())
    except Exception:
        close_temporary_tabs(context, page)
    try:
        with context.expect_page(timeout=15000) as info:
            trigger.click()
        tab = info.value
        tab.wait_for_load_state("domcontentloaded", timeout=TIMEOUT)
        tab.wait_for_timeout(800)
        pdf_links = tab.locator("a[href*='.pdf'], a[href*='.PDF']")
        if pdf_links.count():
            href = pdf_links.first.get_attribute("href")
            response = context.request.get(urljoin(tab.url, href), timeout=TIMEOUT)
            data = response.body()
            if response.ok and data.startswith(b"%PDF"):
                target.write_bytes(data)
                tab.close()
                return str(target.resolve())
        tab.pdf(path=str(target), format="A4", print_background=True)
        tab.close()
        return str(target.resolve()) if target.exists() else "PDF FAILED"
    except Exception as exc:
        # The current Income Tax site opens the document in a same-page
        # viewer/modal (see the Download PDF panel), rather than opening a
        # new tab.  Inspect that viewer before reporting failure.
        try:
            viewer = page.locator(
                "iframe[src], embed[src], object[data], "
                "a[href*='.pdf'], a[href*='.PDF']"
            )
            for i in range(viewer.count()):
                element = viewer.nth(i)
                if not element.is_visible():
                    continue
                pdf_ref = (
                    element.get_attribute("src")
                    or element.get_attribute("data")
                    or element.get_attribute("href")
                )
                if not pdf_ref:
                    continue
                # PDF.js commonly stores the real file in ?file=...
                from urllib.parse import parse_qs, unquote, urlparse
                parsed = urlparse(urljoin(page.url, pdf_ref))
                embedded = parse_qs(parsed.query).get("file", [None])[0]
                pdf_url = unquote(embedded) if embedded else parsed.geturl()
                response = context.request.get(pdf_url, timeout=TIMEOUT)
                data = response.body()
                if response.ok and data.startswith(b"%PDF"):
                    target.write_bytes(data)
                    return str(target.resolve())

            # Some versions expose only a viewer button, which itself emits
            # the download event after the modal has been opened.
            download_button = page.get_by_text(
                re.compile(r"^Download PDF$", re.I)
            ).last
            if download_button.count() and download_button.is_visible():
                with page.expect_download(timeout=15000) as info:
                    download_button.click()
                info.value.save_as(target)
                return str(target.resolve())
        except Exception:
            pass
        return f"ERROR: {type(exc).__name__}: {exc}"
    finally:
        close_temporary_tabs(context, page)


def collect_category(context, page, category, source_url, folder, rows):
    safe_goto(page, HOME_URL, wait_until="commit", timeout=TIMEOUT)
    page.wait_for_timeout(1500)
    safe_goto(page, source_url, wait_until="commit", timeout=TIMEOUT)
    cards = page.locator("#listViewContent .card")
    title_buttons = page.locator("#listViewContent button.card-title")
    for _ in range(16):
        if title_buttons.count() == 0:
            title_buttons = page.locator("button.card-title")
        if title_buttons.count() > 0:
            break
        page.wait_for_timeout(2000)
    if title_buttons.count() == 0:
        raise RuntimeError(f"{category}: document cards did not load from {source_url}")
    seen = set()
    number = 0
    while True:
        cards = page.locator("#listViewContent .card")
        title_buttons = page.locator("#listViewContent button.card-title")
        if title_buttons.count() == 0:
            title_buttons = page.locator("button.card-title")
        count = title_buttons.count()
        for i in range(count):
            title_btn = title_buttons.nth(i)
            card = title_btn.locator("xpath=ancestor::div[contains(@class,'card')][1]")
            if not card.count():
                card = title_btn.locator("xpath=ancestor::*[contains(@class,'card')][1]")
            name = (title_btn.first.get_attribute("title") or title_btn.first.inner_text()).strip()
            key = name + "|" + (card.inner_text() or "")[:150]
            if key in seen:
                continue
            seen.add(key)
            number += 1
            text = card.inner_text() or ""
            date_match = re.search(r"Published On\s*:\s*([^\n]+)", text, re.I)
            record = {"name": name, "category": category, "date": date_match.group(1).strip() if date_match else ""}
            record["pdf_path"] = download_from_card(context, page, title_btn.first, folder, number, name)
            record["status"] = "Newly downloaded" if str(record["pdf_path"]).lower().endswith(".pdf") else "Failed"
            record["timestamp"] = datetime.now().isoformat(timespec="seconds")
            rows.append(record)
            write_audit(rows)
            print(f"{category} {number}: {record['name'][:80]} -> {record['pdf_path']}", flush=True)
            if MAX_ITEMS and len([x for x in rows if x["category"] == category]) >= MAX_ITEMS:
                return
        next_button = visible_next_button(page)
        if next_button is None:
            return
        next_button.click(timeout=20000, force=True)
        page.wait_for_timeout(1800)


def write_audit(rows):
    BASE.mkdir(parents=True, exist_ok=True)
    if AUDIT_FILE.exists():
        workbook = load_workbook(AUDIT_FILE)
        for sheet_name in ("Acts", "Circulars", "Notifications"):
            if sheet_name in workbook.sheetnames:
                del workbook[sheet_name]
    else:
        workbook = Workbook()
        if "Sheet" in workbook.sheetnames:
            del workbook["Sheet"]
    headers = ["Category", "Title", "Published On", "PDF File Name", "File Path", "Download Status", "Timestamp"]
    groups = {
        "Acts": [r for r in rows if str(r.get("category", "")).startswith("Section")],
        "Circulars": [r for r in rows if r.get("category") == "Circular"],
        "Notifications": [r for r in rows if r.get("category") == "Notification"],
    }
    for sheet_name, sheet_rows in groups.items():
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(headers)
        for row in sheet_rows:
            pdf_path = row.get("pdf_path", "")
            sheet.append([
                row.get("category", ""), row.get("name", ""), row.get("date", ""),
                Path(pdf_path).name if pdf_path and not str(pdf_path).startswith(("ERROR", "PDF")) else "",
                pdf_path, row.get("status", ""), row.get("timestamp", ""),
            ])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="2F5597")
            cell.alignment = Alignment(horizontal="center")
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for col, width in {"A": 20, "B": 100, "C": 24, "D": 65, "E": 115, "F": 18, "G": 22}.items():
            sheet.column_dimensions[col].width = width
    workbook.save(AUDIT_FILE)


def collect_act_year(context, page, year, rows):
    """Download every section for one Finance Act amendment year."""
    safe_goto(page, ACT_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
    page.wait_for_timeout(2500)
    year_box = page.get_by_role("combobox").nth(1)
    year_box.click()
    page.get_by_text(year, exact=True).last.click()
    page.wait_for_timeout(1800)
    section_number = 0
    while True:
        buttons = page.locator("button.download[aria-label^='Download PDF for Section']")
        if buttons.count() == 0:
            page.wait_for_timeout(1500)
            buttons = page.locator("button.download[aria-label^='Download PDF for Section']")
        for i in range(buttons.count()):
            section_number += 1
            button = buttons.nth(i)
            title = button.get_attribute("aria-label") or "Section"
            title = re.sub(r"^Download PDF for ", "", title)
            card = button.locator("xpath=ancestor::*[contains(@class,'section') or contains(@class,'card')][1]")
            if card.count():
                text = card.inner_text()
                section_match = re.search(r"(Section\s+\d+)", text, re.I)
                title = section_match.group(1) + " - " + (text.splitlines()[-1].strip() if text.splitlines() else title) if section_match else title
            folder = BASE / f"Act_{year}" / "Sections"
            target = unique_path(folder, f"{int(year):04d}_{section_number:04d}_{safe_filename(title)}.pdf")
            record = {"category": f"Section ({year})", "name": title, "date": "", "timestamp": datetime.now().isoformat(timespec="seconds")}
            try:
                with page.expect_download(timeout=20000) as info:
                    button.click()
                info.value.save_as(target)
                record.update(pdf_path=str(target.resolve()), status="Newly downloaded")
            except Exception as exc:
                record.update(pdf_path=f"ERROR: {exc}", status="Failed")
            rows.append(record); write_audit(rows)
        next_button = visible_next_button(page)
        if next_button is None:
            break
        old_marker = buttons.first.get_attribute("aria-label") if buttons.count() else ""
        next_button.click(timeout=20000, force=True)
        try:
            page.wait_for_function(
                "old => { const e=document.querySelector(\"button.download[aria-label^='Download PDF for Section']\"); return e && e.getAttribute('aria-label') !== old; }",
                old_marker,
                timeout=20000,
            )
        except Exception:
            page.wait_for_timeout(2500)


def save_act_pdf():
    ACT_FOLDER.mkdir(parents=True, exist_ok=True)
    path = ACT_FOLDER / "Income_Tax_Act_2025.pdf"
    if path.exists() and path.stat().st_size > 0:
        return str(path.resolve())
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        safe_goto(page, ACT_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
        page.wait_for_timeout(5000)
        page.pdf(path=str(path), format="A4", print_background=True)
        browser.close()
    return str(path.resolve())


def main():
    rows = []
    write_audit(rows)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=HEADLESS)
        context = browser.new_context(accept_downloads=True, viewport={"width": 1366, "height": 900})
        page = context.new_page()
        for year in ACT_YEARS:
            collect_act_year(context, page, year, rows)
        for category, url in SOURCES.items():
            folder = CIRCULAR_FOLDER if category == "Circular" else NOTIFICATION_FOLDER
            collect_category(context, page, category, url, folder, rows)
            close_temporary_tabs(context, page)
        context.close()
        browser.close()
    write_audit(rows)
    print(f"Acts: {sum(x['category'] == 'Act' for x in rows)}")
    print(f"Circulars: {sum(x['category'] == 'Circular' for x in rows)}")
    print(f"Notifications: {sum(x['category'] == 'Notification' for x in rows)}")
    print(f"Audit: {AUDIT_FILE.resolve()}")


if __name__ == "__main__":
    guarded_main(main)
