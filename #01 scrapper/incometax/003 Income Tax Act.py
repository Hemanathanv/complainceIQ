"""
Income Tax Circulars and Notifications downloader
==================================

Run from PowerShell:
    python "003 Income Tax Act.py"

Collect records without downloading PDFs:
    python "003 Income Tax Act.py" --nodownload

Downloads are stored under:
    IncomeTax/Circulars/
    IncomeTax/Notifications/

The default setting processes all available records. To limit a test run:
    $env:INCOME_TAX_MAX_ITEMS = "10"
"""

import os
import json
import argparse
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright


SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = next(parent / ".env" for parent in Path(__file__).resolve().parents if (parent / "docker-compose.yml").is_file())
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
MILESTONE_NOTIFIED = False
SITE_CHECK = {}
CATEGORY_CHECKS = {}
PRESERVED_PAGES = []
DOWNLOAD_ENABLED = True


def notify_download_milestone(rows, milestone=200):
    """Alert the operator once when the run reaches the download milestone."""
    global MILESTONE_NOTIFIED
    if MILESTONE_NOTIFIED:
        return
    successful = sum(1 for row in rows if row.get("status") == "Newly downloaded")
    if successful < milestone:
        return
    MILESTONE_NOTIFIED = True
    message = f"MILESTONE: {successful} Income Tax PDFs downloaded. Check scraper progress and failures."
    print(f"\n\a{message}\n", flush=True)
    try:
        import winsound
        for _ in range(3):
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            time.sleep(0.6)
    except Exception as direct_error:
        current_info = listing_page_info(page)
        if current_info and current_info[0] == target_page:
            page.wait_for_function(
                "() => document.querySelectorAll('#listViewContent .card-title').length > 0 "
                "|| document.querySelectorAll('.card-title').length > 0",
                timeout=TIMEOUT,
            )
            return "page_input_enter"
        if current_info and current_info[0] not in {current_page, target_page}:
            raise RuntimeError(
                f"Page input landed on unexpected page {current_info[0]} while targeting {target_page}"
            ) from direct_error


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
CIRCULAR_FOLDER = BASE / "Circulars"
NOTIFICATION_FOLDER = BASE / "Notifications"
MANIFEST_FILE = BASE / "manifest.json"

HOME_URL = "https://www.incometaxindia.gov.in/home"
SOURCES = {
    "Circular": "https://www.incometaxindia.gov.in/circulars",
    "Notification": "https://www.incometaxindia.gov.in/notifications",
}
MAX_ITEMS = int(os.environ.get("INCOME_TAX_MAX_ITEMS", "0"))  # 0 = full run
HEADLESS = os.environ.get("INCOME_TAX_HEADLESS", "false").lower() in {"1", "true", "yes"}
TIMEOUT = 60000
LISTING_PAGE_TIMEOUT = 15000
PRINT_OUTCOME_TIMEOUT = 15000
PAGINATION_TIMEOUT = 15000
RESUME_STATE = {"category": "Circular", "page": 1, "file": 1, "complete": False}


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


def card_for_title(title_element):
    """Find the exact .card ancestor (avoiding names like notification-card-width)."""
    card = title_element.locator(
        "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' card ')][1]"
    )
    if card.count():
        return card
    return title_element.locator("xpath=ancestor::*[contains(@class,'card')][1]")


def is_valid_pdf(path):
    """Return true only for a nontrivial file with a PDF header."""
    try:
        candidate = Path(path)
        if not candidate.is_file() or candidate.stat().st_size < 1000:
            return False
        with candidate.open("rb") as stream:
            return stream.read(5) == b"%PDF-"
    except OSError:
        return False


def close_temporary_tabs(context, keep):
    """Close popup/PDF tabs while retaining the homepage and category tabs."""
    for opened in list(context.pages):
        if opened != keep and not any(opened == saved for saved in PRESERVED_PAGES):
            try:
                opened.close()
            except Exception:
                pass


def close_document_viewer(page):
    """Dismiss the same-page document viewer so it cannot block later rows."""
    modal = page.locator(".document-viewer-modal.show")
    if not modal.count():
        return
    close_button = modal.locator(
        "button[aria-label*='close' i], button[title*='close' i], "
        "button.btn-close, .modal-header button"
    ).last
    try:
        if close_button.count() and close_button.is_visible():
            close_button.click(force=True, timeout=5000)
        else:
            page.keyboard.press("Escape")
        modal.wait_for(state="hidden", timeout=5000)
    except Exception:
        try:
            page.keyboard.press("Escape")
            modal.wait_for(state="hidden", timeout=3000)
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


def download_from_card(context, page, trigger, folder, number, name):
    """Download from the viewer already opened by Print, or fall back to the title."""
    folder.mkdir(parents=True, exist_ok=True)
    existing = sorted(folder.glob(f"{number:04d}_*.pdf"))
    if existing and is_valid_pdf(existing[0]):
        close_document_viewer(page)
        return str(existing[0].resolve())
    target = unique_path(folder, f"{number:04d}_{safe_filename(name)}.pdf")
    # Print and the title open the same viewer on the current site. Reuse the
    # viewer opened by monitor_print_action instead of closing it and opening
    # the same document a second time.
    modal = page.locator(".document-viewer-modal.show")
    if modal.count():
        try:
            download_button = modal.locator(
                "button.title-wrap[aria-label^='Download PDF']"
            ).last
            if not download_button.count():
                download_button = modal.locator(
                    "button[aria-label^='Download PDF']"
                ).last
            if not download_button.count():
                download_button = modal.get_by_text(
                    re.compile(r"^Download PDF$", re.I)
                ).last
            if not download_button.count() or not download_button.is_visible():
                close_document_viewer(page)
                return "PDF FAILED: Download PDF control missing from document viewer"
            with page.expect_download(timeout=5000) as info:
                download_button.click(force=True)
            info.value.save_as(target)
            return str(target.resolve()) if is_valid_pdf(target) else "PDF FAILED: downloaded file is not a valid PDF"
        except Exception as exc:
            # Older document entries may not emit a download event from this
            # control. Export the already-open document tab as a PDF instead.
            try:
                open_document = modal.locator("button.btn-open-in-new-tab").last
                if not open_document.count() or not open_document.is_visible():
                    raise RuntimeError("Open In New Tab control missing from document viewer")
                with context.expect_page(timeout=15000) as info:
                    open_document.click(force=True)
                document_tab = info.value
                document_tab.wait_for_load_state("domcontentloaded", timeout=TIMEOUT)
                document_tab.wait_for_timeout(1000)
                document_tab.pdf(path=str(target), format="A4", print_background=True)
                document_tab.close()
                if is_valid_pdf(target):
                    return str(target.resolve())
                return "PDF FAILED: rendered document export did not produce a valid PDF"
            except Exception as fallback_exc:
                close_temporary_tabs(context, page)
                return (f"ERROR: open viewer download failed: {type(exc).__name__}: {exc}; "
                        f"rendered-page PDF fallback failed: {type(fallback_exc).__name__}: {fallback_exc}")
        finally:
            close_document_viewer(page)
    try:
        href = trigger.get_attribute("href")
        if href and ".pdf" in href.lower():
            response = context.request.get(urljoin(page.url, href), timeout=TIMEOUT)
            data = response.body()
            if response.ok and data.startswith(b"%PDF-"):
                target.write_bytes(data)
                return str(target.resolve())
    except Exception:
        close_temporary_tabs(context, page)
    try:
        with page.expect_download(timeout=10000) as info:
            trigger.click()
        info.value.save_as(target)
        return str(target.resolve()) if is_valid_pdf(target) else "PDF FAILED: downloaded file is not a valid PDF"
    except Exception:
        close_temporary_tabs(context, page)
        # The Income Tax site opens its document in a same-page modal. Its
        # Download PDF button is inside that viewer; clicking the card again
        # would be intercepted by the still-open modal.
        modal = page.locator(".document-viewer-modal.show")
        if modal.count():
            try:
                download_button = modal.locator(
                    "button.title-wrap[aria-label^='Download PDF']"
                ).last
                if not download_button.count():
                    download_button = modal.locator(
                        "button[aria-label^='Download PDF']"
                    ).last
                if not download_button.count():
                    download_button = modal.get_by_text(
                        re.compile(r"^Download PDF$", re.I)
                    ).last
                if download_button.count() and download_button.is_visible():
                    with page.expect_download(timeout=5000) as info:
                        download_button.click(force=True)
                    info.value.save_as(target)
                    return str(target.resolve()) if is_valid_pdf(target) else "PDF FAILED: downloaded file is not a valid PDF"
                return "PDF FAILED: Download PDF control missing from document viewer"
            except Exception as exc:
                # Some older circulars render in the viewer but their Download
                # PDF button does not emit a browser download. Open the rendered
                # document in its own tab and save that document as a PDF.
                try:
                    open_document = modal.locator("button.btn-open-in-new-tab").last
                    if not open_document.count() or not open_document.is_visible():
                        raise RuntimeError("Open In New Tab control missing from document viewer")
                    with context.expect_page(timeout=15000) as info:
                        open_document.click(force=True)
                    document_tab = info.value
                    document_tab.wait_for_load_state("domcontentloaded", timeout=TIMEOUT)
                    document_tab.wait_for_timeout(1000)
                    document_tab.pdf(path=str(target), format="A4", print_background=True)
                    document_tab.close()
                    if is_valid_pdf(target):
                        return str(target.resolve())
                    return "PDF FAILED: rendered document export did not produce a valid PDF"
                except Exception as fallback_exc:
                    close_temporary_tabs(context, page)
                    return (f"ERROR: document viewer download failed: {type(exc).__name__}: {exc}; "
                            f"rendered-page PDF fallback failed: {type(fallback_exc).__name__}: {fallback_exc}")
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
            if response.ok and data.startswith(b"%PDF-"):
                target.write_bytes(data)
                tab.close()
                return str(target.resolve())
        tab.pdf(path=str(target), format="A4", print_background=True)
        tab.close()
        return str(target.resolve()) if is_valid_pdf(target) else "PDF FAILED: exported file is not a valid PDF"
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
                if response.ok and data.startswith(b"%PDF-"):
                    target.write_bytes(data)
                    return str(target.resolve())

            # Some versions expose only a viewer button, which itself emits
            # the download event after the modal has been opened.
            download_button = page.get_by_text(
                re.compile(r"^Download PDF$", re.I)
            ).last
            if download_button.count() and download_button.is_visible():
                with page.expect_download(timeout=5000) as info:
                    download_button.click()
                info.value.save_as(target)
                return str(target.resolve()) if is_valid_pdf(target) else "PDF FAILED: downloaded file is not a valid PDF"
        except Exception:
            pass
        return f"ERROR: {type(exc).__name__}: {exc}"
    finally:
        close_document_viewer(page)
        close_temporary_tabs(context, page)


def listing_page_info(page):
    """Return (page number, page count, first item, last item, total items)."""
    body_text = page.locator("body").inner_text()
    match = re.search(
        r"Showing\s+items\s+([\d,]+)\s+to\s+([\d,]+)\s+of\s+([\d,]+)",
        body_text,
        re.I,
    )
    if not match:
        match = re.search(
            r"([\d,]+)\s*-\s*([\d,]+)\s+of\s+([\d,]+)\s+items",
            body_text,
            re.I,
        )
    page_input = page.locator("#pagination-input-box")
    aria = page_input.get_attribute("aria-label") if page_input.count() else ""
    page_match = re.search(r"Page(?:\s+number)?\s+([\d,]+)\s+of\s+([\d,]+)", aria or "", re.I)
    if not match or not page_match:
        return None
    first_item, last_item, total_items = (int(value.replace(",", "")) for value in match.groups())
    page_number, page_count = (int(value.replace(",", "")) for value in page_match.groups())
    return page_number, page_count, first_item, last_item, total_items


def wait_for_listing_page_content(page, expected_page=None, timeout=LISTING_PAGE_TIMEOUT):
    """Wait up to 15 seconds for a page's full advertised set of cards to load."""
    page.wait_for_function(
        "expectedPage => { const text = document.body.innerText; "
        "const m = text.match(/Showing\\s+items\\s+([\\d,]+)\\s+to\\s+([\\d,]+)\\s+of\\s+[\\d,]+/i) "
        "|| text.match(/([\\d,]+)\\s*-\\s*([\\d,]+)\\s+of\\s+[\\d,]+\\s+items/i); "
        "const first = m ? Number(m[1].replace(/,/g, '')) : 0; "
        "const input = document.querySelector('#pagination-input-box'); "
        "const count = m ? Number(m[2].replace(/,/g, '')) - Number(m[1].replace(/,/g, '')) + 1 : 0; "
        "const expectedFirst = expectedPage === null ? first : ((expectedPage - 1) * 10 + 1); "
        "const cards = document.querySelectorAll('#listViewContent .card-title').length "
        "|| document.querySelectorAll('.card-title').length; "
        "return m && first === expectedFirst && count > 0 && cards >= count && "
        "(expectedPage === null || (input && Number(input.value) === expectedPage)); }",
        arg=expected_page,
        timeout=timeout,
    )
    return listing_page_info(page)


def wait_for_circulars_page_load(circulars_page, expected_page=None):
    """Wait up to 15 seconds for all Circulars cards on a page to appear."""
    return wait_for_listing_page_content(circulars_page, expected_page, LISTING_PAGE_TIMEOUT)


def wait_for_notifications_page_load(notifications_page, expected_page=None):
    """Wait up to 15 seconds for all Notifications cards on a page to appear."""
    return wait_for_listing_page_content(notifications_page, expected_page, LISTING_PAGE_TIMEOUT)


def extract_last_reviewed_updated_on(page):
    """Read the site's footer review date, such as '28-Sep-2026'."""
    body_text = page.locator("body").inner_text()
    normalized = body_text.replace("\u00a0", " ")
    match = re.search(
        r"Last\s+reviewed\s+and\s+updated\s+on\s*:\s*([^\r\n]+)",
        normalized,
        re.I,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def verify_homepage(page):
    """Require the official homepage to load before scraping."""
    # The site's guided tour appears briefly on first visits. Install this
    # observer before navigation so its transient close control is not missed.
    page.add_init_script("""(() => {
        const closeTour = () => {
            const close = document.querySelector('#tg-dialog-close-btn');
            if (!close) return false;
            close.click();
            window.__incomeTaxTourClosed = true;
            return true;
        };
        const observe = () => {
            if (closeTour()) return;
            const observer = new MutationObserver(() => {
                if (closeTour()) observer.disconnect();
            });
            observer.observe(document.documentElement, {childList: true, subtree: true});
            window.setTimeout(() => observer.disconnect(), 15000);
        };
        if (document.documentElement) observe();
        else document.addEventListener('DOMContentLoaded', observe, {once: true});
    })();""")
    safe_goto(page, HOME_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
    page.wait_for_function(
        "() => document.body && /Latest Updates/i.test(document.body.innerText) "
        "&& /Explore/i.test(document.body.innerText)",
        timeout=TIMEOUT,
    )
    current = urlparse(page.url)
    if (current.hostname or "").lower() != "www.incometaxindia.gov.in" or current.path.rstrip("/") != "/home":
        raise RuntimeError(f"Homepage check failed: unexpected URL {page.url}")


def dismiss_guided_tour(page):
    """Close the site's first-visit guided tour if its overlay is active."""
    close_control = page.locator("#tg-dialog-close-btn[aria-label='Close Tour']")
    try:
        close_control.wait_for(state="visible", timeout=1500)
        close_control.click(timeout=2000, force=True)
        close_control.wait_for(state="hidden", timeout=3000)
        return True
    except Exception:
        pass
    if page.evaluate("() => Boolean(window.__incomeTaxTourClosed)"):
        return True

    body_text = page.locator("body").inner_text()
    if not (re.search(r"\b1\s*of\s*\d+\b", body_text, re.I)
            and re.search(r"\bNext\b", body_text, re.I)):
        return False

    tour_region = page.locator("div").filter(
        has_text=re.compile(r"\b1\s*of\s*\d+\b", re.I)
    ).filter(has=page.get_by_role("button", name=re.compile(r"^Next$", re.I))).last
    close_selectors = (
        "button[aria-label*='close' i]",
        "button[title*='close' i]",
        "[role='button'][aria-label*='close' i]",
        "[role='button'][title*='close' i]",
        ".introjs-skipbutton",
        ".shepherd-cancel-icon",
        ".driver-popover-close-btn",
        "[class*='tour' i] button[class*='close' i]",
        "[class*='tour' i] [role='button'][aria-label*='close' i]",
    )
    for selector in close_selectors:
        candidates = tour_region.locator(selector)
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            try:
                if candidate.is_visible():
                    candidate.click(timeout=2500, force=True)
                    page.wait_for_timeout(400)
                    remaining = page.locator("body").inner_text()
                    if not (re.search(r"\b1\s*of\s*\d+\b", remaining, re.I)
                            and re.search(r"\bNext\b", remaining, re.I)):
                        return True
            except Exception:
                continue

    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    remaining = page.locator("body").inner_text()
    if not (re.search(r"\b1\s*of\s*\d+\b", remaining, re.I)
            and re.search(r"\bNext\b", remaining, re.I)):
        return True
    raise RuntimeError("The site's Guided Tour overlay is active and could not be dismissed")


def verify_site_date(page):
    """Require and return the homepage footer's last-reviewed date."""
    site_date = extract_last_reviewed_updated_on(page)
    if not site_date:
        raise RuntimeError("Homepage loaded, but its 'Last reviewed and updated on' date was not found")
    return site_date


def verify_tax_laws_rules_element(page):
    """Require the visible 'Tax Laws & Rules' navigation element on the homepage."""
    elements = page.get_by_text("Tax Laws & Rules", exact=True)
    for index in range(elements.count()):
        try:
            if elements.nth(index).is_visible():
                return True
        except Exception:
            continue
    raise RuntimeError("Homepage check failed: visible 'Tax Laws & Rules' element was not found")


def hover_tax_laws_rules_element(page):
    """Move the browser pointer over the visible 'Tax Laws & Rules' element."""
    elements = page.get_by_text("Tax Laws & Rules", exact=True)
    for index in range(elements.count()):
        element = elements.nth(index)
        try:
            if element.is_visible():
                element.hover(timeout=10000, force=True)
                return True
        except Exception:
            continue
    raise RuntimeError("Could not move pointer over the 'Tax Laws & Rules' element")


def verify_circulars_notifications_element(page):
    """Require the visible submenu entry for Circulars and Notifications."""
    elements = page.get_by_text("Circulars and Notifications", exact=True)
    for index in range(elements.count()):
        try:
            elements.nth(index).wait_for(state="visible", timeout=10000)
            if elements.nth(index).is_visible():
                return True
        except Exception:
            continue
    raise RuntimeError(
        "Homepage check failed: visible 'Circulars and Notifications' entry was not found"
    )


def hover_circulars_notifications_element(page):
    """Move the browser pointer over the Circulars and Notifications submenu entry."""
    elements = page.get_by_text("Circulars and Notifications", exact=True)
    for index in range(elements.count()):
        element = elements.nth(index)
        try:
            element.wait_for(state="visible", timeout=10000)
            element.hover(timeout=10000, force=True)
            return True
        except Exception:
            continue
    raise RuntimeError("Could not move pointer over 'Circulars and Notifications'")


def _category_menu_link(page, label, expected_url):
    """Find a category anchor in the homepage menu and validate its destination."""
    links = page.locator("a[href]")
    expected_path = urlparse(expected_url).path.rstrip("/").lower()
    for index in range(links.count()):
        link = links.nth(index)
        try:
            href = link.get_attribute("href")
            if not href:
                continue
            absolute_url = urljoin(page.url, href)
            parsed = urlparse(absolute_url)
            if ((parsed.hostname or "").lower().endswith("incometaxindia.gov.in")
                    and parsed.path.rstrip("/").lower() == expected_path):
                link_text = re.sub(r"\s+", " ", link.inner_text()).strip()
                if not link_text or label.casefold() in link_text.casefold():
                    return absolute_url
        except Exception:
            continue
    raise RuntimeError(f"Homepage menu check failed: '{label}' link to {expected_url} was not found")


def verify_circular_link(page):
    """Check that the submenu contains its Circulars destination link."""
    return _category_menu_link(page, "Circulars", SOURCES["Circular"])


def verify_notification_link(page):
    """Check that the submenu contains its Notifications destination link."""
    return _category_menu_link(page, "Notifications", SOURCES["Notification"])


def _open_category_in_new_tab(context, category, source_url):
    """Open and verify a category listing in a separate browser tab."""
    tab = context.new_page()
    try:
        safe_goto(tab, source_url, wait_until="domcontentloaded", timeout=TIMEOUT)
        if category == "Circulars":
            wait_for_circulars_page_load(tab, expected_page=1)
        else:
            wait_for_notifications_page_load(tab, expected_page=1)
        info = listing_page_info(tab)
        if not info or info[0] != 1:
            raise RuntimeError(f"{category}: new tab did not open its first listing page")
        expected_path = urlparse(source_url).path.rstrip("/").lower()
        actual = urlparse(tab.url)
        if (actual.hostname or "").lower() != "www.incometaxindia.gov.in" or actual.path.rstrip("/").lower() != expected_path:
            raise RuntimeError(f"{category}: unexpected new-tab URL {tab.url}")
        return tab
    except Exception:
        try:
            tab.close()
        except Exception:
            pass
        raise


def open_circular_in_new_tab(context, source_url=None):
    """Open the Circulars listing in its own browser tab."""
    return _open_category_in_new_tab(context, "Circulars", source_url or SOURCES["Circular"])


def open_notification_in_new_tab(context, source_url=None):
    """Open the Notifications listing in its own browser tab."""
    return _open_category_in_new_tab(context, "Notifications", source_url or SOURCES["Notification"])


def open_notifications_from_home_after_circulars(context, home_page):
    """Return Home, reopen the Tax Laws menu, and open Notifications in a new tab."""
    verify_homepage(home_page)
    hover_tax_laws_rules_element(home_page)
    verify_circulars_notifications_element(home_page)
    hover_circulars_notifications_element(home_page)
    notification_link = verify_notification_link(home_page)
    notification_page = open_notification_in_new_tab(context, notification_link)
    return notification_link, notification_page


def verify_circular_listing_url(page):
    """Check that the Circulars tab is on the official Circulars URL."""
    parsed = urlparse(page.url)
    if ((parsed.hostname or "").lower() != "www.incometaxindia.gov.in"
            or parsed.path.rstrip("/").lower() != "/circulars"):
        raise RuntimeError(f"Circulars URL check failed: {page.url}")
    return page.url


def verify_notification_listing_url(page):
    """Check that the Notifications tab is on the official Notifications URL."""
    parsed = urlparse(page.url)
    if ((parsed.hostname or "").lower() != "www.incometaxindia.gov.in"
            or parsed.path.rstrip("/").lower() != "/notifications"):
        raise RuntimeError(f"Notifications URL check failed: {page.url}")
    return page.url


def read_listing_last_updated(page):
    """Read the listing's own 'Last Updated' value, separate from the footer date."""
    text = page.locator("body").inner_text()
    match = re.search(r"Last\s+Updated\s*:\s*([^\r\n]+)", text, re.I)
    if not match:
        raise RuntimeError(f"{page.url}: listing 'Last Updated' value was not found")
    return re.sub(r"\s+", " ", match.group(1)).strip()


def read_listing_total_items(page):
    """Read the total result count reported by the listing paginator."""
    info = listing_page_info(page)
    if not info:
        raise RuntimeError(f"{page.url}: could not read total item count")
    return info[4]


def read_listing_item_range(page):
    """Read the first and last item numbers shown on the current listing page."""
    info = listing_page_info(page)
    if not info:
        raise RuntimeError(f"{page.url}: could not read visible item range")
    return info[2], info[3]


def read_listing_page_position(page):
    """Read the current page number and total number of listing pages."""
    info = listing_page_info(page)
    if not info:
        raise RuntimeError(f"{page.url}: could not read current pagination position")
    return info[0], info[1]


def _wait_for_listing_page(page, target_page, old_first_item, old_last_item, forward, timeout=PAGINATION_TIMEOUT):
    page.wait_for_function(
        "args => { const input = document.querySelector('#pagination-input-box'); "
        "const text = document.body.innerText; "
        "const match = text.match(/Showing\\s+items\\s+([\\d,]+)\\s+to\\s+([\\d,]+)\\s+of/i) "
        "|| text.match(/([\\d,]+)\\s*-\\s*([\\d,]+)\\s+of\\s+[\\d,]+\\s+items/i); "
        "const first = match ? Number(match[1].replace(/,/g, '')) : 0; "
        "const last = match ? Number(match[2].replace(/,/g, '')) : 0; "
        "const cards = document.querySelectorAll('#listViewContent .card-title').length "
        "|| document.querySelectorAll('.card-title').length; "
        "const expected = last - first + 1; "
        "const changed = args.forward ? first > args.last : first < args.first; "
        "return input && Number(input.value) === args.page && changed && expected > 0 && cards >= expected; }",
        arg={"page": target_page, "first": old_first_item, "last": old_last_item,
             "forward": forward},
        timeout=timeout,
    )


def _go_to_listing_page(page, target_page):
    """Set a listing page, preferring Next/Previous for an adjacent page."""
    info = listing_page_info(page)
    if not info:
        raise RuntimeError("Could not read current listing position before navigation")
    current_page, page_count, old_first_item, old_last_item, _ = info
    if target_page < 1 or target_page > page_count:
        raise ValueError(f"Requested page {target_page} is outside 1-{page_count}")
    if target_page == current_page:
        return "already_on_page"

    step = 1 if target_page > current_page else -1
    navigation_error = None
    if abs(target_page - current_page) == 1:
        button_id = "#pagination-next-button" if step > 0 else "#pagination-previous-button"
        button = page.locator(button_id)
        if not button.count():
            button = visible_next_button(page) if step > 0 else page.get_by_role("button", name="Previous", exact=True).first
        if button and button.count() and button.is_visible() and not button.is_disabled():
            try:
                button.click(timeout=15000, force=True)
                _wait_for_listing_page(
                    page, target_page, old_first_item, old_last_item,
                    forward=target_page > current_page,
                )
                return "next_button" if step > 0 else "previous_button"
            except Exception as error:
                navigation_error = error

    # Numeric entry is the fallback, and remains available for resume jumps.
    try:
        page_input = page.locator("#pagination-input-box")
        if page_input.count():
            page_input.fill(str(target_page), timeout=10000)
            page_input.press("Enter", timeout=10000)
            _wait_for_listing_page(
                page, target_page, old_first_item, old_last_item,
                forward=target_page > current_page,
            )
            return "page_input_enter"
    except Exception as direct_error:
        current_info = listing_page_info(page)
        if current_info and current_info[0] == target_page:
            card_count = page.locator("#listViewContent .card-title").count()
            if card_count == 0:
                card_count = page.locator(".card-title").count()
            expected_count = current_info[3] - current_info[2] + 1
            if card_count >= expected_count:
                return "page_input_enter"
        if current_info and current_info[0] not in {current_page, target_page}:
            raise RuntimeError(
                f"Navigation landed on unexpected page {current_info[0]} while targeting {target_page}"
            ) from direct_error
        navigation_error = direct_error
    raise RuntimeError(
        f"Could not navigate from page {current_page} to page {target_page}: {navigation_error}"
    )


def verify_listing_navigator(page):
    """Test page 2 through Enter or Next, then restore page 1 for the scraper."""
    info = listing_page_info(page)
    if not info:
        raise RuntimeError("Pagination preflight could not read the current page")
    if info[0] != 1 or info[1] < 2:
        raise RuntimeError(f"Pagination preflight expected page 1 with a next page; found {info[0]} of {info[1]}")
    method = _go_to_listing_page(page, 2)
    page_two = listing_page_info(page)
    if not page_two or page_two[0] != 2 or page_two[2:4] != (11, 20):
        raise RuntimeError("Pagination preflight did not load items 11-20 on page 2")
    _go_to_listing_page(page, 1)
    restored = listing_page_info(page)
    if not restored or restored[0] != 1 or restored[2:4] != (1, 10):
        raise RuntimeError("Pagination preflight could not restore the listing to page 1")
    return {
        "method": method,
        "tested_page": page_two[0],
        "tested_item_range": [page_two[2], page_two[3]],
        "restored_page": restored[0],
    }


def extract_listing_titles_and_dates(page, category):
    """Capture every visible title and Published On date on the current page."""
    title_buttons = page.locator("#listViewContent .card-title")
    if title_buttons.count() == 0:
        title_buttons = page.locator(".card-title")
    info = listing_page_info(page)
    if not info or title_buttons.count() == 0:
        raise RuntimeError(f"{category}: titles and dates could not be read from the current page")
    first_item = info[2]
    result = []
    for index in range(title_buttons.count()):
        title_button = title_buttons.nth(index)
        card = card_for_title(title_button)
        title = (title_button.get_attribute("title") or title_button.inner_text()).strip()
        href = title_button.get_attribute("href") or ""
        card_text = card.inner_text() or ""
        date_match = re.search(r"Published\s+On\s*:\s*([^\r\n]+)", card_text, re.I)
        result.append({
            "item_number": first_item + index,
            "title": title,
            "published_on": re.sub(r"\s+", " ", date_match.group(1)).strip() if date_match else "",
            "item_url": urljoin(page.url, href) if href else "",
        })
    return result


def _inspect_category_listing(page, category):
    """Run the shared 11-19 checks for a category's identical listing UI."""
    if category == "Circular":
        listing_url = verify_circular_listing_url(page)
    else:
        listing_url = verify_notification_listing_url(page)
    if category == "Circular":
        wait_for_circulars_page_load(page, expected_page=1)
    else:
        wait_for_notifications_page_load(page, expected_page=1)
    last_updated = read_listing_last_updated(page)
    total_items = read_listing_total_items(page)
    item_range = read_listing_item_range(page)
    page_position = read_listing_page_position(page)
    page_items = extract_listing_titles_and_dates(page, category)
    if item_range != (1, 10):
        raise RuntimeError(
            f"{category} first-page range was {item_range[0]}-{item_range[1]}, expected 1-10"
        )
    if page_position[0] != 1:
        raise RuntimeError(f"{category} opened on page {page_position[0]}, expected page 1")
    navigator_check = verify_listing_navigator(page)
    return {
        "url": listing_url,
        "last_updated": last_updated,
        "total_items": total_items,
        "visible_item_range": list(item_range),
        "current_page": page_position[0],
        "total_pages": page_position[1],
        "page_items": page_items,
        "navigator_check": navigator_check,
    }


def inspect_circulars_listing(circulars_page):
    """Check Circulars URL, update date, counts, first-page records, and navigation."""
    return _inspect_category_listing(circulars_page, "Circular")


def inspect_notifications_listing(notifications_page):
    """Check Notifications URL, update date, counts, first-page records, and navigation."""
    return _inspect_category_listing(notifications_page, "Notification")


def monitor_circular_print_click(context, circulars_page, circular_card):
    """Click and log a Circular's Print action, including popup/download behavior."""
    return monitor_print_action(context, circulars_page, circular_card)


def monitor_notification_print_click(context, notifications_page, notification_card):
    """Click and log a Notification's Print action, including popup/download behavior."""
    return monitor_print_action(context, notifications_page, notification_card)


def monitor_print_action(context, page, card):
    """Click one card's Print control and record downloads or popup PDF URLs."""
    print_button = card.get_by_role("button", name=re.compile(r"^Print\b", re.I))
    if print_button.count() == 0:
        return {
            "result": "print_button_missing",
            "new_tab_urls": [],
            "new_tab_loads": [],
            "download_names": [],
            "same_tab_url": "",
        }

    before_pages = list(context.pages)
    before_url = page.url
    before_info = listing_page_info(page)
    before_page_number = before_info[0] if before_info else 1
    downloads = []
    popup_pages = []

    def capture_download(download):
        downloads.append(download)

    def capture_popup(opened):
        popup_pages.append(opened)
        opened.on("download", capture_download)

    page.on("download", capture_download)
    context.on("page", capture_popup)
    click_error = ""
    try:
        print_button.first.click(timeout=10000)
        deadline = time.monotonic() + PRINT_OUTCOME_TIMEOUT / 1000
        while time.monotonic() < deadline:
            new_pages = [opened for opened in context.pages if opened not in before_pages]
            if (new_pages or downloads or page.url != before_url
                    or page.locator(".document-viewer-modal.show").count()):
                break
            page.wait_for_timeout(200)
    except Exception as error:
        click_error = f"{type(error).__name__}: {error}"
    finally:
        page.remove_listener("download", capture_download)
        context.remove_listener("page", capture_popup)
        for opened in popup_pages:
            try:
                opened.remove_listener("download", capture_download)
            except Exception:
                pass

    new_pages = list(dict.fromkeys(
        [opened for opened in context.pages if opened not in before_pages] + popup_pages
    ))
    new_tab_urls = []
    new_tab_loads = []
    for opened in new_pages:
        loaded = False
        try:
            opened.wait_for_load_state("domcontentloaded", timeout=PRINT_OUTCOME_TIMEOUT)
            loaded = True
        except Exception:
            pass
        try:
            tab_url = opened.url
        except Exception:
            tab_url = ""
        new_tab_urls.append(tab_url)
        new_tab_loads.append({"url": tab_url, "loaded_within_15_seconds": loaded})

    download_names = []
    for download in downloads:
        try:
            download_names.append(download.suggested_filename)
        except Exception:
            download_names.append("")

    same_tab_url = ""
    if new_tab_urls and download_names:
        result = "new_tab_and_download_event"
    elif new_tab_urls:
        result = "new_tab"
    elif download_names:
        result = "download_event"
    elif page.url != before_url:
        result = "same_tab_navigation"
        same_tab_url = page.url
    elif page.locator(".document-viewer-modal.show").count():
        result = "same_page_document_viewer"
    elif click_error:
        result = "click_error"
    else:
        result = "no_response_within_15_seconds"

    if result == "same_tab_navigation":
        navigated_url = page.url
        safe_goto(page, before_url, wait_until="commit", timeout=TIMEOUT)
        wait_for_listing_page_content(page, timeout=LISTING_PAGE_TIMEOUT)
        restored_info = listing_page_info(page)
        if restored_info and restored_info[0] != before_page_number:
            _go_to_listing_page(page, before_page_number)
        print(f"Print probe navigated current tab to {navigated_url}; listing restored.", flush=True)

    close_temporary_tabs(context, page)
    return {
        "result": result,
        "new_tab_urls": new_tab_urls,
        "new_tab_loads": new_tab_loads,
        "download_names": download_names,
        "same_tab_url": same_tab_url,
        "error": click_error,
    }


def capture_document_url_without_download(context, page, title_button):
    """Open a document through its title, capture its viewer URL, and close it without saving a file."""
    before_pages = list(context.pages)
    before_url = page.url
    popups = []
    download_names = []

    def cancel_download(download):
        try:
            download_names.append(download.suggested_filename)
        except Exception:
            download_names.append("")
        try:
            download.cancel()
        except Exception:
            pass

    def capture_popup(popup):
        if popup not in popups:
            popups.append(popup)
            popup.on("download", cancel_download)

    page.on("popup", capture_popup)
    context.on("page", capture_popup)
    modal = page.locator(".document-viewer-modal.show")
    error = ""
    try:
        page.on("download", cancel_download)
        title_button.click(timeout=5000, force=True)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if (popups or any(opened not in before_pages for opened in context.pages)
                    or modal.count() or page.url != before_url):
                break
            page.wait_for_timeout(100)
    except Exception as caught:
        error = f"{type(caught).__name__}: {caught}"
    finally:
        page.remove_listener("popup", capture_popup)
        context.remove_listener("page", capture_popup)
        page.remove_listener("download", cancel_download)

    new_pages = [opened for opened in context.pages if opened not in before_pages]
    popup = popups[0] if popups else (new_pages[0] if new_pages else None)
    if popup is None and modal.count():
        open_tab_button = modal.locator("button.btn-open-in-new-tab")
        if open_tab_button.count() and open_tab_button.is_visible():
            try:
                with page.expect_popup(timeout=5000) as popup_info:
                    open_tab_button.click(timeout=5000)
                popup = popup_info.value
                capture_popup(popup)
            except Exception as caught:
                error = f"{type(caught).__name__}: {caught}"

    if popup:
        try:
            document_url = popup.url
        except Exception:
            document_url = ""
        if not document_url or document_url == "about:blank":
            try:
                popup.wait_for_function(
                    "() => location.href && location.href !== 'about:blank'",
                    timeout=2000,
                )
                document_url = popup.url
            except Exception:
                pass
        try:
            popup.close()
        except Exception:
            pass
        close_document_viewer(page)
        return {
            "result": "document_url_captured_without_download" if document_url and document_url != "about:blank" else "document_url_missing",
            "document_url": document_url,
            "url_captured": bool(document_url and document_url != "about:blank"),
            "document_page_load_waited": False,
            "download_names_cancelled": download_names,
            "error": error,
        }

    if page.url != before_url:
        document_url = page.url
        try:
            safe_goto(page, before_url, wait_until="commit", timeout=TIMEOUT)
            wait_for_listing_page_content(page, timeout=LISTING_PAGE_TIMEOUT)
        except Exception as caught:
            error = f"{type(caught).__name__}: {caught}"
        return {
            "result": "same_tab_document_opened_without_download",
            "document_url": document_url,
            "url_captured": bool(document_url),
            "document_page_load_waited": False,
            "download_names_cancelled": download_names,
            "error": error,
        }

    if modal.count():
        document_url = modal.evaluate("m => { const e=m.querySelector('iframe,embed,object'); return e ? (e.src || e.data || '') : ''; }")
        close_document_viewer(page)
        return {
            "result": "document_viewer_opened_without_download" if document_url else "document_viewer_without_exposed_url",
            "document_url": document_url,
            "url_captured": bool(document_url),
            "document_page_load_waited": False,
            "download_names_cancelled": download_names,
            "error": error,
        }

    return {
        "result": "document_url_not_opened",
        "document_url": "",
        "url_captured": False,
        "document_page_load_waited": False,
        "download_names_cancelled": download_names,
        "error": error,
    }


def collect_category(context, page, category, source_url, folder, rows, page_already_open=False):
    """Download records from a chosen page/file position, then continue page by page."""
    wait_for_category_page = (
        wait_for_circulars_page_load if category == "Circular"
        else wait_for_notifications_page_load
    )
    start_page = int(RESUME_STATE["page"]) if category == RESUME_STATE["category"] else 1
    start_file = int(RESUME_STATE["file"]) if category == RESUME_STATE["category"] else 1
    if not page_already_open:
        safe_goto(page, HOME_URL, wait_until="commit", timeout=TIMEOUT)
        page.wait_for_timeout(1500)
        safe_goto(page, source_url, wait_until="commit", timeout=TIMEOUT)
    title_buttons = page.locator("#listViewContent .card-title")
    try:
        wait_for_category_page(page, expected_page=1)
    except Exception as error:
        raise RuntimeError(f"{category}: listing page did not fully load within 15 seconds") from error
    if title_buttons.count() == 0:
        title_buttons = page.locator(".card-title")
    processed_count = 0
    last_reviewed_updated_on = ""
    review_date_page = None
    while True:
        try:
            info = wait_for_category_page(page)
        except Exception as error:
            raise RuntimeError(f"{category}: current listing page did not load within 15 seconds") from error
        if not info:
            raise RuntimeError(f"{category}: could not read current page and total-item counts")
        page_number, page_count, first_item, last_item, total_items = info
        if page_number != review_date_page:
            last_reviewed_updated_on = extract_last_reviewed_updated_on(page)
            review_date_page = page_number
        cards = page.locator("#listViewContent .card")
        title_buttons = page.locator("#listViewContent .card-title")
        if title_buttons.count() == 0:
            title_buttons = page.locator(".card-title")
        count = title_buttons.count()
        LOG_LINE = (f"{category}: page {page_number}/{page_count}, showing "
                    f"{first_item}-{last_item} of {total_items} items ({count} visible)")
        print(LOG_LINE, flush=True)
        if page_number < start_page:
            advance_listing_page(page, page_number, last_item)
            continue
        first_file = start_file - 1 if page_number == start_page else 0
        if first_file >= count:
            raise RuntimeError(
                f"{category}: file {start_file} is outside page {page_number} ({count} visible files)"
            )
        for i in range(first_file, count):
            title_btn = title_buttons.nth(i)
            card = card_for_title(title_btn)
            name = (title_btn.first.get_attribute("title") or title_btn.first.inner_text()).strip()
            number = first_item + i
            text = card.inner_text() or ""
            date_match = re.search(r"Published On\s*:\s*([^\n]+)", text, re.I)
            record = {
                "name": name,
                "item_url": "",
                "category": category,
                "date": date_match.group(1).strip() if date_match else "",
                "page": page_number,
                "file_on_page": i + 1,
                "item_number": number,
                "page_items": last_item - first_item + 1,
                "total_items": total_items,
                "total_pages": page_count,
                "last_reviewed_updated_on": last_reviewed_updated_on,
            }
            if not DOWNLOAD_ENABLED:
                # Inventory-only mode reads listing metadata without opening
                # each title or Print control, so it does not probe document URLs.
                record["print_probe"] = {
                    "result": "skipped_nodownload",
                    "new_tab_urls": [],
                    "new_tab_loads": [],
                    "download_names": [],
                    "same_tab_url": "",
                }
                record["document_link_probe"] = {
                    "result": "skipped_nodownload_metadata_only",
                    "document_url": "",
                    "url_captured": False,
                    "document_page_load_waited": False,
                    "download_names_cancelled": [],
                    "error": "",
                }
                record["document_url"] = ""
            elif category == "Circular":
                record["print_probe"] = monitor_circular_print_click(context, page, card)
            else:
                record["print_probe"] = monitor_notification_print_click(context, page, card)
            # Until this attempt finishes, the saved cursor remains on this
            # record. An interruption retries it and reuses a valid existing PDF.
            RESUME_STATE.update(category=category, page=page_number, file=i + 1, complete=False)
            if DOWNLOAD_ENABLED:
                record["pdf_path"] = download_from_card(context, page, title_btn.first, folder, number, name)
                record["status"] = "Newly downloaded" if is_valid_pdf(record["pdf_path"]) else "Failed"
            else:
                previous = next((row for row in rows
                                 if row.get("category") == category
                                 and row.get("item_number") == number), None)
                if previous and previous.get("status") == "Newly downloaded" \
                        and is_valid_pdf(previous.get("pdf_path", "")):
                    # A no-download inventory pass must not downgrade an
                    # already verified PDF in the manifest.
                    record["pdf_path"] = previous["pdf_path"]
                    record["status"] = previous["status"]
                else:
                    record["pdf_path"] = ""
                    record["status"] = "Not downloaded (--nodownload)"
            record["timestamp"] = datetime.now().isoformat(timespec="seconds")
            upsert_manifest_record(rows, record)
            if DOWNLOAD_ENABLED and record["status"] == "Newly downloaded":
                notify_download_milestone(rows, 200)
            # Always advance after recording the result. Failed records remain
            # in the manifest's errors list for review and a later retry pass.
            advance_resume_cursor(category, page_number, i + 1, count, page_count)
            write_manifest(rows)
            print(
                f"{category} {number}: {record['status']} | {record['name'][:80]} -> {record['pdf_path']}",
                flush=True,
            )
            print(
                f"Print probe {category} {number}: {record['print_probe']['result']}; "
                f"tab_loads={record['print_probe']['new_tab_loads']} "
                f"same_tab_url={record['print_probe']['same_tab_url']} "
                f"downloads={record['print_probe']['download_names']}",
                flush=True,
            )
            if not DOWNLOAD_ENABLED:
                print(
                    f"Document link {category} {number}: "
                    f"{record['document_link_probe']['result']} | {record['document_url']}",
                    flush=True,
                )
            if record["status"] == "Failed":
                print(f"{category}: failed item recorded at page {page_number}, file {i + 1}; continuing", flush=True)
            processed_count += 1
            if MAX_ITEMS and processed_count >= MAX_ITEMS:
                return False
        if page_number >= page_count:
            return True
        advance_listing_page(page, page_number, last_item)


def collect_circulars(context, circulars_page, rows):
    """Run the page-by-page Circulars download workflow."""
    return collect_category(
        context, circulars_page, "Circular", SOURCES["Circular"],
        CIRCULAR_FOLDER, rows, page_already_open=True,
    )


def collect_notifications(context, notifications_page, rows):
    """Run the same page-by-page workflow for Notifications."""
    return collect_category(
        context, notifications_page, "Notification", SOURCES["Notification"],
        NOTIFICATION_FOLDER, rows, page_already_open=True,
    )


def advance_listing_page(page, old_page, old_last_item):
    last_error = None
    for attempt in range(2):
        try:
            _go_to_listing_page(page, old_page + 1)
            info = listing_page_info(page)
            card_count = page.locator("#listViewContent .card-title").count()
            if card_count == 0:
                card_count = page.locator(".card-title").count()
            if info and info[0] == old_page + 1 and info[3] > old_last_item and card_count:
                return
            raise RuntimeError(f"Navigation did not load records after item {old_last_item}")
        except Exception as exc:
            last_error = exc
            # A slow page update can time out after the site already advanced.
            info = listing_page_info(page)
            if info and info[0] > old_page and info[3] > old_last_item:
                card_count = page.locator("#listViewContent .card-title").count()
                if card_count == 0:
                    card_count = page.locator(".card-title").count()
                if card_count:
                    return
                raise RuntimeError(f"Page {info[0]} advanced but its document cards did not load") from exc
            if attempt == 0:
                time.sleep(2)
    raise RuntimeError(f"Next-page navigation failed at page {old_page}: {last_error}")


def upsert_manifest_record(rows, record):
    key = (record.get("category"), record.get("item_number"))
    for index, old in enumerate(rows):
        old_key = (old.get("category"), old.get("item_number"))
        if old_key == key and key[1] is not None:
            rows[index] = record
            return
    rows.append(record)


def advance_resume_cursor(category, page_number, file_number, visible_count, page_count):
    if file_number < visible_count:
        RESUME_STATE.update(category=category, page=page_number, file=file_number + 1, complete=False)
    elif page_number < page_count:
        RESUME_STATE.update(category=category, page=page_number + 1, file=1, complete=False)
    elif category == "Circular":
        RESUME_STATE.update(category="Notification", page=1, file=1, complete=False)
    else:
        RESUME_STATE.update(category="Notification", page=page_number, file=file_number, complete=True)


def write_manifest(rows):
    """Write an atomic JSON inventory of discovered documents and download results."""
    BASE.mkdir(parents=True, exist_ok=True)
    successful = [row for row in rows if row.get("status") == "Newly downloaded"]
    failed = [row for row in rows if row.get("status") == "Failed"]
    manifest = {
        "baseUrl": "https://www.incometaxindia.gov.in/",
        "tax": "Income Tax",
        "scope": "Income Tax Circulars and Notifications",
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "site_check": SITE_CHECK,
        "category_checks": CATEGORY_CHECKS,
        "discovered": len(rows),
        "downloaded": len(successful),
        "resume": RESUME_STATE,
        "errors": [
            {"category": row.get("category", ""), "title": row.get("name", ""),
             "message": row.get("pdf_path", "Download failed")}
            for row in failed
        ],
        "files": rows,
    }
    temporary = MANIFEST_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    last_error = None
    for attempt in range(6):
        try:
            temporary.replace(MANIFEST_FILE)
            last_error = None
            break
        except PermissionError as error:
            last_error = error
            time.sleep(0.2 * (attempt + 1))
    if last_error:
        raise last_error


def load_manifest():
    """Load earlier results and the last saved resume cursor, if available."""
    if not MANIFEST_FILE.exists():
        return [], {"category": "Circular", "page": 1, "file": 1, "complete": False}
    try:
        data = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        rows = data.get("files", []) if isinstance(data.get("files", []), list) else []
        saved = data.get("resume")
        if isinstance(saved, dict) and saved.get("category") in SOURCES:
            return rows, {
                "category": saved["category"],
                "page": max(1, int(saved.get("page", 1))),
                "file": max(1, int(saved.get("file", 1))),
                "complete": bool(saved.get("complete", False)),
            }

        page_positions = {}
        for row in rows:
            category, page_number = row.get("category"), row.get("page")
            if category in SOURCES and page_number:
                key = (category, int(page_number))
                page_positions[key] = page_positions.get(key, 0) + 1
                row.setdefault("file_on_page", page_positions[key])
                row.setdefault("item_number", (int(page_number) - 1) * 10 + page_positions[key])
        failed = next((row for row in rows if row.get("status") == "Failed"), None)
        if failed:
            return rows, {
                "category": failed["category"], "page": int(failed.get("page", 1)),
                "file": int(failed.get("file_on_page", 1)), "complete": False,
            }
        if rows:
            last = rows[-1]
            category = last.get("category", "Circular")
            page_number = int(last.get("page", 1))
            file_number = int(last.get("file_on_page", 1))
            if file_number < int(last.get("page_items", 10)):
                return rows, {"category": category, "page": page_number,
                              "file": file_number + 1, "complete": False}
            if category == "Circular":
                return rows, {"category": "Notification", "page": 1, "file": 1, "complete": False}
            return rows, {"category": "Notification", "page": page_number,
                          "file": file_number, "complete": True}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return [], {"category": "Circular", "page": 1, "file": 1, "complete": False}


def prompt_resume(saved):
    """Ask where to continue; Enter accepts the saved point."""
    if saved.get("complete"):
        prompt = "Previous run is complete. Enter a new start as 'Circular 1 1' or 'Notification 1 1'; Enter to exit: "
        response = input(prompt).strip()
        if not response:
            return None
    else:
        prompt = (f"Resume at {saved['category']} page {saved['page']} file {saved['file']}? "
                  "Press Enter to accept, or enter '<Circular|Notification> <page> <file>': ")
        response = input(prompt).strip()
        if not response:
            return saved

    match = re.fullmatch(r"\s*(Circular|Notification|C|N)?\s*(\d+)\s+(\d+)\s*", response, re.I)
    if not match:
        raise ValueError("Enter a resume point like: Circular 26 1")
    category_token, page_text, file_text = match.groups()
    if category_token:
        category = "Circular" if category_token.lower().startswith("c") else "Notification"
    else:
        category = saved.get("category", "Circular")
    return {"category": category, "page": max(1, int(page_text)),
            "file": max(1, int(file_text)), "complete": False}


def main():
    global RESUME_STATE
    parser = argparse.ArgumentParser(
        description="Collect Income Tax Circulars and Notifications. Downloads PDFs by default."
    )
    parser.add_argument(
        "--nodownload",
        action="store_true",
        help="collect and log listing details without clicking Print or downloading PDFs",
    )
    args = parser.parse_args()
    global DOWNLOAD_ENABLED
    DOWNLOAD_ENABLED = not args.nodownload
    if args.nodownload:
        print("No-download mode: collecting records and updating the manifest; PDF actions are disabled.", flush=True)
    rows, saved_resume = load_manifest()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=HEADLESS)
        context = browser.new_context(accept_downloads=True, viewport={"width": 1366, "height": 900})
        try:
            page = context.new_page()
            verify_homepage(page)
            tour_dismissed = dismiss_guided_tour(page)
            site_date = verify_site_date(page)
            tax_laws_rules_present = verify_tax_laws_rules_element(page)
            tax_laws_rules_hovered = hover_tax_laws_rules_element(page)
            circulars_notifications_present = verify_circulars_notifications_element(page)
            circulars_notifications_hovered = hover_circulars_notifications_element(page)
            circular_link = verify_circular_link(page)
            notification_link = verify_notification_link(page)
            circular_page = open_circular_in_new_tab(context, circular_link)
            PRESERVED_PAGES.clear()
            PRESERVED_PAGES.extend([page, circular_page])
            circular_checks = inspect_circulars_listing(circular_page)
            CATEGORY_CHECKS.clear()
            CATEGORY_CHECKS["Circular"] = circular_checks
            SITE_CHECK.clear()
            SITE_CHECK.update({
                "homepage_url": page.url,
                "guided_tour_dismissed": tour_dismissed,
                "last_reviewed_updated_on": site_date,
                "tax_laws_rules_present": tax_laws_rules_present,
                "tax_laws_rules_hovered": tax_laws_rules_hovered,
                "circulars_notifications_present": circulars_notifications_present,
                "circulars_notifications_hovered": circulars_notifications_hovered,
                "circular_link": circular_link,
                "notification_link": notification_link,
                "circular_tab_url": circular_page.url,
                "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            })
            write_manifest(rows)
            print(
                f"Homepage verified; site last reviewed and updated on: {site_date}; "
                f"Circulars listing: Last Updated {circular_checks['last_updated']}, "
                f"{circular_checks['total_items']} items, items "
                f"{circular_checks['visible_item_range'][0]}-"
                f"{circular_checks['visible_item_range'][1]}, page "
                f"{circular_checks['current_page']} of {circular_checks['total_pages']}; "
                f"captured {len(circular_checks['page_items'])} titles and publication dates; "
                f"navigation tested by {circular_checks['navigator_check']['method']} and restored to page 1. "
                "Circulars opened in a separate tab.",
                flush=True,
            )

            selected_resume = prompt_resume(saved_resume)
            if selected_resume is None:
                print("Previous run is complete; no additional download started.")
                return
            RESUME_STATE = selected_resume
            write_manifest(rows)
            start_notifications = RESUME_STATE["category"] == "Notification"
            if RESUME_STATE["category"] == "Circular":
                start_notifications = collect_circulars(context, circular_page, rows)
                close_temporary_tabs(context, page)

            if start_notifications:
                notification_link, notification_page = open_notifications_from_home_after_circulars(
                    context, page
                )
                PRESERVED_PAGES.append(notification_page)
                notification_checks = inspect_notifications_listing(notification_page)
                CATEGORY_CHECKS["Notification"] = notification_checks
                SITE_CHECK.update({
                    "notification_link": notification_link,
                    "notification_tab_url": notification_page.url,
                })
                write_manifest(rows)
                print(
                    f"Circulars complete; returned Home and opened Notifications in a new tab. "
                    f"Notifications: Last Updated {notification_checks['last_updated']}, "
                    f"{notification_checks['total_items']} items, items "
                    f"{notification_checks['visible_item_range'][0]}-"
                    f"{notification_checks['visible_item_range'][1]}, page "
                    f"{notification_checks['current_page']} of {notification_checks['total_pages']}; "
                    f"captured {len(notification_checks['page_items'])} titles and publication dates.",
                    flush=True,
                )
                collect_notifications(context, notification_page, rows)
                close_temporary_tabs(context, page)
        finally:
            context.close()
            browser.close()
    write_manifest(rows)
    print(f"Circulars: {sum(x['category'] == 'Circular' for x in rows)}")
    print(f"Notifications: {sum(x['category'] == 'Notification' for x in rows)}")
    print(f"Failed items recorded for review: {sum(x.get('status') == 'Failed' for x in rows)}")
    if not DOWNLOAD_ENABLED:
        print(f"Items discovered without downloading: {sum(x.get('status') == 'Not downloaded (--nodownload)' for x in rows)}")
    print(f"Manifest: {MANIFEST_FILE.resolve()}")


if __name__ == "__main__":
    guarded_main(main)
