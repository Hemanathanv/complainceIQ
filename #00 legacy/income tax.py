"""
Income Tax India (incometaxindia.gov.in) — Bulk PDF Downloader
================================================================

Automates:
  1. Income-tax Act 2025
       -> Chapters tab -> expand every chapter -> download every section PDF
       -> paginate ("Next") until the last page
  2. Circulars
       -> open every circular -> download its PDF -> paginate
  3. Notifications
       -> open every notification -> download its PDF -> paginate

Requirements
------------
    pip install playwright
    playwright install chromium

Run
---
    python incometax_pdf_downloader.py

Output
------
    ./downloads/acts/...
    ./downloads/circulars/...
    ./downloads/notifications/...

IMPORTANT NOTES
---------------
- Selectors below are based on the page structure you supplied. Government
  sites change markup fairly often — if a selector stops matching, re-check
  the live DOM (right-click -> Inspect) and adjust the CSS/text selector.
- `fdprocessedid` attributes are NOT used as selectors because they are
  regenerated on every page load (jQuery form-processing markers) and are
  not reliable. Selectors here rely on class names, ARIA labels and visible
  text instead.
- The script runs headed (headless=False) by default. Government portals
  often rate-limit or block headless/bot-like traffic — keep it headed and
  keep the built-in waits/slow_mo if you run into blocks or captchas.
- If a "Download PDF" action opens a new browser tab showing a native PDF
  viewer instead of triggering a file download, the script detects the new
  tab, grabs the PDF bytes via an HTTP request to that tab's URL (reusing
  the browser context's cookies) and saves it to disk.
"""

import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from playwright.sync_api import (
    sync_playwright,
    Page,
    BrowserContext,
    TimeoutError as PWTimeoutError,
)

BASE_URL = "https://www.incometaxindia.gov.in/home"

# Direct URLs, taken straight from the hrefs in the site's nav markup.
# Navigating straight to these is far more reliable than simulating clicks
# through a hover-triggered dropdown menu.
ACT_URL = "https://www.incometaxindia.gov.in/income-tax-act-2025"
CIRCULARS_URL = "https://www.incometaxindia.gov.in/circulars"
NOTIFICATIONS_URL = "https://www.incometaxindia.gov.in/notifications"

DOWNLOAD_ROOT = Path("./downloads")
LOG_PATH = DOWNLOAD_ROOT / "run_log.txt"
NAV_TIMEOUT_MS = 45_000
CLICK_TIMEOUT_MS = 15_000
SLOW_MO_MS = 150  # small delay between actions to look less bot-like / let SPA settle
PAGE_RENDER_RETRY_WAIT_MS = 1000
MAX_EMPTY_PAGE_RETRIES = 5  # guards against counting cards before the SPA finishes rendering a new page


def log(msg: str) -> None:
    """Print to console AND append to a log file, flushed immediately, so
    nothing is lost to terminal scrollback truncation (common on Windows
    Terminal / PowerShell with long runs)."""
    print(msg)
    try:
        ensure_dir(LOG_PATH.parent)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def safe_filename(name: str, max_len: int = 150) -> str:
    """Turn an arbitrary title into a filesystem-safe filename."""
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    return name[:max_len] if len(name) > max_len else name


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_already_downloaded(dest_dir: Path, fallback_name: str) -> Optional[Path]:
    """
    Check whether a PDF for this item was already downloaded in a previous
    run. Matches by the safe-filename stem (ignoring exact suffix/extension
    differences that can happen between the two download methods).
    Returns the existing Path if found, else None.
    """
    if not dest_dir.exists():
        return None
    stem = safe_filename(fallback_name)
    # Exact stem match first (covers the new-tab method's fixed naming)
    exact = dest_dir / f"{stem}.pdf"
    if exact.exists():
        return exact
    # Fuzzy match (covers the download-event method's suggested_filename,
    # which may differ slightly from our fallback_name)
    for existing in dest_dir.glob("*.pdf"):
        if existing.stem[:60] == stem[:60]:
            return existing
    return None


def save_pdf_via_download_event(page: Page, trigger, dest_dir: Path, fallback_name: str) -> bool:
    """
    Click `trigger` (a Locator) and try to capture a native Playwright
    `download` event. Returns True if a file was saved this way.
    """
    try:
        with page.expect_download(timeout=CLICK_TIMEOUT_MS) as dl_info:
            trigger.click()
        download = dl_info.value
        suggested = download.suggested_filename or f"{safe_filename(fallback_name)}.pdf"
        dest = ensure_dir(dest_dir) / safe_filename(suggested)
        download.save_as(dest)
        log(f"    [downloaded] {dest}")
        return True
    except PWTimeoutError:
        return False


def save_pdf_via_direct_href(context: BrowserContext, trigger, dest_dir: Path, fallback_name: str) -> bool:
    """
    If `trigger` (or a link inside/around it) has an href pointing straight
    at a .pdf, fetch that URL directly instead of clicking anything. This
    avoids the click/new-tab dance entirely and is the most reliable method
    when it applies.
    """
    try:
        href = trigger.get_attribute("href")
        if not href:
            # Sometimes the clickable element is a <button> wrapping/near an <a>
            link = trigger.locator("xpath=ancestor-or-self::*[self::a][1] | following-sibling::a[1] | .//a[1]")
            if link.count() > 0:
                href = link.first.get_attribute("href")
        if not href or ".pdf" not in href.lower():
            return False

        full_url = href if href.startswith("http") else urljoin("https://www.incometaxindia.gov.in", href)
        resp = context.request.get(full_url, timeout=NAV_TIMEOUT_MS)
        if not resp.ok:
            return False
        dest = ensure_dir(dest_dir) / f"{safe_filename(fallback_name)}.pdf"
        dest.write_bytes(resp.body())
        log(f"    [downloaded via direct href] {dest}")
        return True
    except Exception:
        return False


def save_pdf_via_new_tab(context: BrowserContext, page: Page, trigger, dest_dir: Path, fallback_name: str) -> bool:
    """
    Click `trigger`, wait for a new tab to open (common for 'open PDF in
    viewer' buttons), then fetch the PDF bytes from that tab's URL using
    the context's request API (shares cookies/session) and save to disk.
    """
    try:
        with context.expect_page(timeout=CLICK_TIMEOUT_MS) as new_page_info:
            trigger.click()
        new_page = new_page_info.value
        new_page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        pdf_url = new_page.url

        resp = context.request.get(pdf_url)
        if resp.ok and "pdf" in (resp.headers.get("content-type", "").lower() + pdf_url.lower()):
            dest = ensure_dir(dest_dir) / f"{safe_filename(fallback_name)}.pdf"
            dest.write_bytes(resp.body())
            log(f"    [downloaded via tab] {dest}")
        else:
            # Not a direct PDF response (e.g. viewer wrapper) -> print the PDF instead
            dest = ensure_dir(dest_dir) / f"{safe_filename(fallback_name)}.pdf"
            new_page.pdf(path=str(dest))
            log(f"    [rendered-to-pdf via tab] {dest}")

        new_page.close()
        return True
    except PWTimeoutError:
        return False
    except Exception as e:
        log(f"    [!] new-tab download failed: {e}")
        return False


def download_pdf(context: BrowserContext, page: Page, trigger, dest_dir: Path, fallback_name: str,
                  max_attempts: int = 2) -> None:
    """
    Skip if already downloaded; otherwise try, in order:
      1. Direct href fetch (fastest, most reliable when it applies)
      2. Native Playwright download event
      3. New-tab detection + fetch/render
    Retries the whole sequence up to `max_attempts` times before giving up,
    since government sites occasionally hiccup on a given click.
    """
    existing = file_already_downloaded(dest_dir, fallback_name)
    if existing is not None:
        log(f"    [skip] already downloaded -> {existing}")
        return

    for attempt in range(1, max_attempts + 1):
        if save_pdf_via_direct_href(context, trigger, dest_dir, fallback_name):
            return
        if save_pdf_via_download_event(page, trigger, dest_dir, fallback_name):
            return
        if save_pdf_via_new_tab(context, page, trigger, dest_dir, fallback_name):
            return
        if attempt < max_attempts:
            log(f"    [retry {attempt}/{max_attempts - 1}] {fallback_name}")
            page.wait_for_timeout(1200)

    log(f"    [!] could not download after {max_attempts} attempts: {fallback_name}")


def click_next_page(page: Page) -> bool:
    """
    Click the pagination 'Next' button if present and enabled.
    Returns True if it navigated to a new page, False if we're at the end.
    """
    next_btn = page.locator("#pagination-next-button")
    if next_btn.count() == 0:
        return False
    if not next_btn.first.is_visible():
        return False
    disabled = next_btn.first.get_attribute("disabled")
    aria_disabled = next_btn.first.get_attribute("aria-disabled")
    classes = next_btn.first.get_attribute("class") or ""
    if disabled is not None or aria_disabled == "true" or "disabled" in classes:
        return False

    try:
        next_btn.first.click(timeout=CLICK_TIMEOUT_MS)
        page.wait_for_timeout(1800)  # let the SPA list re-render
        return True
    except PWTimeoutError:
        return False


def count_with_retry(page: Page, selector: str, max_retries: int = MAX_EMPTY_PAGE_RETRIES,
                      wait_ms: int = PAGE_RENDER_RETRY_WAIT_MS):
    """
    Count elements matching `selector`, retrying with waits if the count is
    0. This guards against the SPA reporting 0 items simply because the new
    page hadn't finished rendering yet after a pagination click — the exact
    bug that silently skipped whole pages of results in earlier runs.
    Returns the Locator and the final count.
    """
    locator = page.locator(selector)
    count = locator.count()
    attempt = 0
    while count == 0 and attempt < max_retries:
        page.wait_for_timeout(wait_ms)
        locator = page.locator(selector)
        count = locator.count()
        attempt += 1
    return locator, count


def goto_section(page: Page, url: str) -> None:
    """
    Navigate directly to a section URL instead of simulating clicks through
    the site's hover-triggered dropdown menu (which is not reliably
    clickable via automation since submenus only render on :hover).
    """
    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    page.wait_for_timeout(1500)


# --------------------------------------------------------------------------
# 1. Income-tax Act 2025 — Chapters -> Sections -> PDFs (with pagination)
# --------------------------------------------------------------------------

def process_income_tax_act(context: BrowserContext, page: Page) -> None:
    log("\n=== Income-tax Act, 2025 ===")
    dest_dir = DOWNLOAD_ROOT / "acts" / "income_tax_act_2025"

    goto_section(page, ACT_URL)
    act_page = page

    # Switch to the "Chapters" tab/radio
    try:
        act_page.locator("#Chapters").click(timeout=CLICK_TIMEOUT_MS)
    except PWTimeoutError:
        act_page.get_by_text("Chapters", exact=False).first.click(timeout=CLICK_TIMEOUT_MS)
    act_page.wait_for_timeout(800)

    page_num = 1
    while True:
        log(f"  -- Chapters listing, page {page_num} --")
        chapter_buttons, count = count_with_retry(act_page, "li.chapters-item button.chapter-name-wrap")
        log(f"    Found {count} chapters on this page")

        for i in range(count):
            try:
                btn = chapter_buttons.nth(i)
                chapter_title = btn.inner_text().strip().replace("\n", " ")
                log(f"  Chapter: {chapter_title}")

                # Expand chapter if collapsed
                expanded = btn.get_attribute("aria-expanded")
                if expanded != "true":
                    btn.click(timeout=CLICK_TIMEOUT_MS)
                    act_page.wait_for_timeout(900)  # give the section list time to render

                chapter_li = act_page.locator("li.chapters-item").nth(i)

                # Strategy 1: same "sections-item" list component used on the
                # Rules page (name in .section-name, a dedicated .download button)
                section_items = chapter_li.locator("li.sections-item")
                n_items = section_items.count()

                chapter_dir = dest_dir / safe_filename(chapter_title)

                if n_items > 0:
                    for s in range(n_items):
                        item = section_items.nth(s)
                        name_el = item.locator(".section-name")
                        name = name_el.first.inner_text().strip() if name_el.count() > 0 else f"section {s + 1}"
                        desc_el = item.locator(".section-desc")
                        desc = desc_el.first.inner_text().strip() if desc_el.count() > 0 else ""
                        label = f"{chapter_title} - {name}" + (f" - {desc}" if desc else "")

                        dl_btn = item.locator("button.download, button:has-text('Download PDF')")
                        if dl_btn.count() == 0:
                            # fall back to any element with 'download' in it
                            dl_btn = item.locator("[class*='download'], button:has-text('Download')")
                        if dl_btn.count() == 0:
                            log(f"    [!] no download control found for {name}, skipping")
                            continue
                        download_pdf(context, act_page, dl_btn.first, chapter_dir, label)
                else:
                    # Strategy 2: generic fallback — any clickable element whose
                    # text/attributes mention 'Download' inside this chapter block
                    generic_dl = chapter_li.locator(
                        "button:has-text('Download'), a:has-text('Download'), [aria-label*='Download' i]"
                    )
                    n_generic = generic_dl.count()
                    if n_generic > 0:
                        for s in range(n_generic):
                            label = f"{chapter_title} - section {s + 1}"
                            download_pdf(context, act_page, generic_dl.nth(s), chapter_dir, label)
                    else:
                        # Nothing matched — dump a snippet so we can diagnose the
                        # real markup instead of silently skipping.
                        log(f"    [!] no downloadable sections detected for '{chapter_title}'.")
                        try:
                            snippet = chapter_li.inner_html()[:1500]
                            log("    ---- chapter HTML snippet (first 1500 chars) ----")
                            log(f"    {snippet}")
                            log("    ---- end snippet ----")
                        except Exception:
                            pass

                # Collapse back (optional, keeps DOM smaller) — ignore failures
                try:
                    if btn.get_attribute("aria-expanded") == "true":
                        btn.click(timeout=3000)
                        act_page.wait_for_timeout(200)
                except Exception:
                    pass
            except Exception as e:
                log(f"    [!] error processing chapter {i + 1} on this page, skipping it: {e}")
                continue

        if not click_next_page(act_page):
            break
        page_num += 1

    log("=== Done: Income-tax Act, 2025 ===")


# --------------------------------------------------------------------------
# 2. Circulars / Notifications — card list, click title -> download PDF
# --------------------------------------------------------------------------

def process_card_listing(context: BrowserContext, page: Page, level3_label: str, folder: str, url: str) -> None:
    log(f"\n=== {level3_label} ===")
    dest_dir = DOWNLOAD_ROOT / folder

    goto_section(page, url)

    page_num = 1
    while True:
        log(f"  -- {level3_label} listing, page {page_num} --")
        cards, count = count_with_retry(page, "#listViewContent .card")
        log(f"    Found {count} entries on this page")

        for i in range(count):
            try:
                card = cards.nth(i)
                title_btn = card.locator("button.card-title")
                if title_btn.count() == 0:
                    continue
                title = title_btn.first.get_attribute("title") or title_btn.first.inner_text().strip()
                title = title.strip()
                log(f"  {title[:100]}...")

                # Clicking the title typically opens a detail page / new tab with the PDF.
                download_pdf(context, page, title_btn.first, dest_dir, title)
            except Exception as e:
                log(f"    [!] error processing entry {i + 1} on this page, skipping it: {e}")
                continue

        if not click_next_page(page):
            break
        page_num += 1

    log(f"=== Done: {level3_label} ===")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    ensure_dir(DOWNLOAD_ROOT)
    log(f"\n================ RUN START {datetime.now().isoformat(timespec='seconds')} ================")
    log(f"Full log is being written to: {LOG_PATH.resolve()}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=SLOW_MO_MS)
        context = browser.new_context(accept_downloads=True)
        context.set_default_timeout(NAV_TIMEOUT_MS)
        page = context.new_page()

        log(f"Navigating to {BASE_URL}")
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(1500)

        # 1) Acts -> Income-tax Act, 2025 (chapters + sections)
        try:
            process_income_tax_act(context, page)
        except Exception:
            log(f"[!] Error while processing Income-tax Act 2025:\n{traceback.format_exc()}")

        # Return to a clean base page before the next section (also picks up
        # the first tab again in case the Act opened in a new one)
        page = context.pages[0]
        page.bring_to_front()

        # 2) Circulars and Notifications -> Circulars
        try:
            process_card_listing(context, page, "Circulars", "circulars", CIRCULARS_URL)
        except Exception:
            log(f"[!] Error while processing Circulars:\n{traceback.format_exc()}")

        # 3) Circulars and Notifications -> Notifications
        try:
            process_card_listing(context, page, "Notifications", "notifications", NOTIFICATIONS_URL)
        except Exception:
            log(f"[!] Error while processing Notifications:\n{traceback.format_exc()}")

        log("\nAll done. Check the ./downloads folder.")
        log(f"Full run log saved at: {LOG_PATH.resolve()}")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()