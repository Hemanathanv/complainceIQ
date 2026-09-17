
from pathlib import Path
from urllib.parse import urljoin
import re
import time
from playwright.sync_api import sync_playwright
START_URL = "https://wcd.gov.in/documents/legislations"
WCD = Path("WCD")
NOTIFICATION = WCD / "notification"
ACT = WCD / "act"
RULES = WCD / "rules"
for folder in (NOTIFICATION, ACT, RULES):
    folder.mkdir(parents=True, exist_ok=True)
def clean_name(name):
    name = re.sub(r'[<>:"/\\|?*]', '_', name.strip())
    return name.rstrip(" .") or "document"
def valid_pdf(path):
    try:
        return (
            path.exists()
            and path.stat().st_size > 1000
            and path.read_bytes()[:5] == b"%PDF-"
        )
    except:
        return False
def scroll_page(page):
    last = 0
    while True:
        height = page.evaluate(
            "document.documentElement.scrollHeight"
        )
        page.evaluate(
            "window.scrollTo(0, document.documentElement.scrollHeight)"
        )
        time.sleep(1)
        new_height = page.evaluate(
            "document.documentElement.scrollHeight"
        )
        if new_height == last:
            break
        last = new_height
    page.evaluate("window.scrollTo(0, 0)")
def download(page, url, folder, title):
    filename = clean_name(title)
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    path = folder / filename
    if valid_pdf(path):
        print(f"✓ Already exists: {filename}")
        return True
    for attempt in range(1, 4):
        try:
            print(f"↓ [{attempt}/3] {filename}")
            response = page.context.request.get(
                url,
                timeout=60000
            )
            data = response.body()
            if response.ok and data.startswith(b"%PDF-"):
                path.write_bytes(data)
                if valid_pdf(path):
                    print(f"✓ Saved: {filename}")
                    return True
        except Exception as e:
            print(f"  Retry error: {e}")
        time.sleep(2)
    print(f"✗ FAILED: {filename}")
    return False
def download_notifications(page):
    print("\n========== NOTIFICATIONS ==========")
    page.goto(
        START_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )
    page.wait_for_timeout(2000)
    offerings = page.get_by_text(
        "Offerings",
        exact=True
    )
    if offerings.count():
        offerings.first.click()
        page.wait_for_timeout(1000)
    notification = page.locator(
        "a[href*='/offerings/whatsnew']"
    ).first
    if not notification.count():
        print("✗ Notifications link not found")
        return
    notification.click()
    page.wait_for_timeout(2500)
    scroll_page(page)
    links = page.locator("a[href*='.pdf']")
    print(f"PDF links found: {links.count()}")
    for i in range(links.count()):
        try:
            link = links.nth(i)
            href = link.get_attribute("href")
            if not href:
                continue
            url = urljoin(page.url, href)
            row = link.locator(
                "xpath=ancestor::*["
                "self::tr or "
                "contains(@class,'list_det_bx') or "
                "contains(@class,'row')"
                "][1]"
            )
            title = ""
            if row.count():
                paragraphs = row.locator("p")
                for j in range(paragraphs.count()):
                    text = paragraphs.nth(j).inner_text().strip()
                    if text and text.lower() not in {
                        "view",
                        "notification",
                        "notifications"
                    }:
                        title = text
                        break
            if not title:
                title = f"notification_{i + 1}"
            print(f"\nNotification: {title}")
            download(
                page,
                url,
                NOTIFICATION,
                title
            )
        except Exception as e:
            print(f"Notification error: {e}")
def download_acts_rules(page):
    print("\n========== ACTS + RULES ==========")
    processed = set()
    failed = []
    for page_no in range(1, 6):
        url = (
            START_URL
            if page_no == 1
            else f"{START_URL}?page={page_no}"
        )
        print(f"\n--- Page {page_no} ---")
        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000
            )
            page.wait_for_timeout(2000)
            scroll_page(page)
        except Exception as e:
            print(f"✗ Page error: {e}")
            continue
        rows = page.locator(
            "div.list_det_bx.tender_list"
        )
        print(f"Rows: {rows.count()}")
        for i in range(rows.count()):
            try:
                row = rows.nth(i)
                category = row.locator(
                    ".col-md-2 p"
                ).first.inner_text().strip().upper()
                title = row.locator(
                    ".col-md-5 p"
                ).first.inner_text().strip()
                link = row.locator(
                    "a[href*='.pdf']"
                ).first
                if not link.count():
                    print(f"✗ PDF missing: {title}")
                    failed.append(title)
                    continue
                href = link.get_attribute("href")
                if not href:
                    failed.append(title)
                    continue
                pdf_url = urljoin(
                    page.url,
                    href
                )
                if pdf_url in processed:
                    continue
                processed.add(pdf_url)
                if category == "ACTS":
                    folder = ACT
                    label = "ACT"
                elif category == "RULES":
                    folder = RULES
                    label = "RULE"
                else:
                    print(
                        f"? Unknown category: {category}"
                    )
                    continue
                print(f"\n{label}: {title}")
                if not download(
                    page,
                    pdf_url,
                    folder,
                    title
                ):
                    failed.append(title)
            except Exception as e:
                print(
                    f"Row {i + 1} error: {e}"
                )
    print("\n========== ACT/RULE SUMMARY ==========")
    print(f"Processed PDFs : {len(processed)}")
    print(f"Failed PDFs    : {len(failed)}")
    if failed:
        print("\nFAILED:")
        for item in failed:
            print(f" - {item}")
    else:
        print("✓ All Acts and Rules downloaded")
def verify(folder, name):
    files = list(folder.glob("*.pdf"))
    bad = [f.name for f in files if not valid_pdf(f)]
    print(
        f"{name}: {len(files)} PDFs | "
        f"{len(bad)} invalid"
    )
    if bad:
        for item in bad:
            print(f"  ✗ {item}")
    return not bad
def main():
    with sync_playwright() as p:
        print("\n======================================")
        print("WCD PDF DOWNLOADER")
        print("======================================")
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=[
                "--start-maximized",
                "--window-position=0,0"
            ]
        )
        context = browser.new_context(
            viewport=None
        )
        page = context.new_page()
        try:
            page.evaluate("""
                () => {
                    window.moveTo(0, 0);
                    window.resizeTo(
                        screen.availWidth,
                        screen.availHeight
                    );
                }
            """)
        except:
            pass
        download_notifications(page)
        download_acts_rules(page)
        print("\n========== FINAL VERIFICATION ==========")
        n_ok = verify(
            NOTIFICATION,
            "Notifications"
        )
        a_ok = verify(
            ACT,
            "Acts"
        )
        r_ok = verify(
            RULES,
            "Rules"
        )
        print("\n======================================")
        if n_ok and a_ok and r_ok:
            print("✓ ALL DOWNLOADED PDFs VERIFIED")
        else:
            print("⚠ CHECK FAILED/INVALID PDFs")
        print("======================================")
        print("\nFolders:")
        print("WCD/notification")
        print("WCD/act")
        print("WCD/rules")
        print("\nClosing Chrome...")
        browser.close()
        print("Chrome closed.")
if __name__ == "__main__":
    main()
