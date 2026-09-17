
from pathlib import Path
import re
import time
from playwright.sync_api import sync_playwright
PAGE_URL = "https://cpcb.gov.in/hazardous-waste-rules/"
ROOT_FOLDER = Path.cwd() / "CPCB"
HAZARDOUS_WASTE_RULES_FOLDER = ROOT_FOLDER / "Hazardous waste Rules"
OTHER_WASTE_FOLDER = HAZARDOUS_WASTE_RULES_FOLDER / "Other waste"
HAZARDOUS_WASTE_FOLDER = HAZARDOUS_WASTE_RULES_FOLDER / "Hazardous waste"
CIRCULAR_PAGE_URL = "https://cpcb.gov.in/circular/"
CIRCULARS_FOLDER = ROOT_FOLDER / "Circulars"
for folder in [
    OTHER_WASTE_FOLDER,
    HAZARDOUS_WASTE_FOLDER,
    CIRCULARS_FOLDER,
]:
    folder.mkdir(parents=True, exist_ok=True)
OTHER_WASTE_PDFS = [
    (
        "../uploads/hwmd/July_Amendment_HOWM.pdf",
        "First Amendments Rules, 06.07.2016"
    ),
    (
        "../uploads/hwmd/Feb_Amendment_HOWM.pdf",
        "Second Amendments Rules, 28.02.2017"
    ),
    (
        "../uploads/hwmd/June_Amendemnet_HOWM.pdf",
        "Third Amendments Rules, 11.06.2018"
    ),
    (
        "../uploads/hwmd/March_Amendment_HOWM.pdf",
        "Fourth Amendments Rules, 01.03.2019"
    ),
    (
        "../uploads/hwmd/HOWM-Fifth-Amendment-Rules-2020.pdf",
        "Fifth Amendments Rules, 09.10.2020"
    ),
    (
        "../uploads/hwmd/HOWM-Second-Amendment-Rules-2021.pdf",
        "Second Amendments Rules, 12.11.2021"
    ),
    (
        "../uploads/hwmd/HOWM-Sixth-Amendment-Rules-2022.pdf",
        "Sixth Amendments Rules, 21.07.2022"
    ),
]
HAZARDOUS_WASTE_PDFS = [
    (
        "../displaypdf.php?id="
        "aHdtZC8xc3RfQW1lbmRtZW50c19SdWxlcy5wZGY=",
        "First Amendments Rules, 21.07.2009"
    ),
    (
        "../displaypdf.php?id="
        "aHdtZC8ybmRfQW1lbmRtZW50c19SdWxlcy5wZGY=",
        "Second Amendments Rules, 23.09.2009"
    ),
    (
        "../displaypdf.php?id="
        "aHdtZC8zcmRfQW1lbmRtZW50X1J1bGVzLnBkZg==",
        "Third Amendments Rules, 30.03.2010"
    ),
    (
        "../displaypdf.php?id="
        "aHdtZC80dGhfQW1lbmRtZW50c19SdWxlcy5wZGY=",
        "Fourth Amendments Rules, 13.08.2010"
    ),
]
def clean_filename(filename):
    filename = filename.strip()
    filename = re.sub(
        r'[<>:"/\\|?*]',
        '_',
        filename
    )
    filename = re.sub(
        r'\s+',
        ' ',
        filename
    )
    filename = filename.strip(" .")
    if not filename:
        filename = "document"
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    return filename
FAILED_DOWNLOADS = []
def save_pdf_from_link(
    page,
    context,
    href,
    filename,
    save_folder,
    number,
    total,
    section_label="",
    max_retries=3
):
    pdf_page = None
    print("\n==================================================")
    print(f"[{number}/{total}] {filename}")
    print("==================================================")
    try:
        pdf_url = page.evaluate(
            """
            href => new URL(
                href,
                document.baseURI
            ).href
            """,
            href
        )
    except Exception as e:
        print(f"COULD NOT RESOLVE URL: {e}")
        FAILED_DOWNLOADS.append(
            (section_label, href, filename, save_folder)
        )
        return False
    print("PDF URL:")
    print(pdf_url)
    try:
        with context.expect_page(timeout=5000) as new_page_info:
            page.locator(
                f'a[href="{href}"]'
            ).first.click()
        pdf_page = new_page_info.value
        try:
            pdf_page.wait_for_load_state(
                "domcontentloaded",
                timeout=5000
            )
        except Exception:
            pass
        print("PDF OPENED IN BROWSER TAB")
        time.sleep(2)
        print("2 SECONDS COMPLETED")
    except Exception as open_error:
        print(f"PDF OPEN WARNING (continuing anyway): {open_error}")
    target_filename = clean_filename(filename)
    target_path = save_folder / target_filename
    if target_path.exists():
        print(f"ALREADY SAVED — skipping duplicate save: {target_path}")
        if pdf_page is not None:
            try:
                pdf_page.close()
                print("PDF TAB CLOSED")
            except Exception:
                pass
        return True
    saved = False
    for attempt in range(1, max_retries + 1):
        try:
            response = context.request.get(
                pdf_url,
                timeout=60000
            )
            print(
                f"Attempt {attempt}/{max_retries} — "
                f"Response Status: {response.status}"
            )
            if response.ok:
                body = response.body()
                if not body:
                    raise Exception("Empty response body")
                save_folder.mkdir(
                    parents=True,
                    exist_ok=True
                )
                with open(target_path, "wb") as file:
                    file.write(body)
                if target_path.stat().st_size == 0:
                    raise Exception("Saved file is 0 bytes")
                print("PDF SAVED SUCCESSFULLY")
                print(f"Location: {target_path}")
                saved = True
                break
            else:
                print(
                    f"DOWNLOAD FAILED: "
                    f"{response.status} "
                    f"{response.status_text}"
                )
        except Exception as e:
            print(f"ERROR ON ATTEMPT {attempt}: {e}")
        if attempt < max_retries:
            wait_time = attempt * 2
            print(f"Retrying in {wait_time}s...")
            time.sleep(wait_time)
    if pdf_page is not None:
        try:
            pdf_page.close()
            print("PDF TAB CLOSED")
        except Exception:
            pass
    if saved:
        return True
    print(f"GIVING UP FOR NOW AFTER {max_retries} ATTEMPTS: {filename}")
    FAILED_DOWNLOADS.append(
        (section_label, pdf_url, filename, save_folder)
    )
    return False
def retry_failed_downloads(context, max_retries=3):
    if not FAILED_DOWNLOADS:
        print("\nNo failed downloads to retry — everything saved cleanly.")
        return
    print("\n\n")
    print("##################################################")
    print(f"# FINAL RETRY PASS — {len(FAILED_DOWNLOADS)} PDF(S) TO RECHECK")
    print("##################################################")
    pending = FAILED_DOWNLOADS[:]
    FAILED_DOWNLOADS.clear()
    still_failed = []
    for section_label, pdf_url, filename, save_folder in pending:
        print(f"\nRetrying: [{section_label}] {filename}")
        target_filename = clean_filename(filename)
        target_path = save_folder / target_filename
        if target_path.exists():
            print(f"ALREADY SAVED — skipping (no duplicate): {target_path}")
            continue
        recovered = False
        for attempt in range(1, max_retries + 1):
            try:
                response = context.request.get(pdf_url, timeout=60000)
                if response.ok:
                    body = response.body()
                    if body:
                        save_folder.mkdir(parents=True, exist_ok=True)
                        with open(target_path, "wb") as file:
                            file.write(body)
                        if target_path.stat().st_size > 0:
                            print(f"RECOVERED: {target_path}")
                            recovered = True
                            break
            except Exception as e:
                print(f"Retry attempt {attempt} error: {e}")
            time.sleep(attempt * 2)
        if not recovered:
            print(f"STILL FAILED: {filename}")
            still_failed.append(
                (section_label, pdf_url, filename, save_folder)
            )
    if still_failed:
        print(
            f"\n{len(still_failed)} PDF(s) could NOT be downloaded "
            "even after the final retry pass:"
        )
        for section_label, pdf_url, filename, save_folder in still_failed:
            print(f"  - [{section_label}] {filename}")
            print(f"      {pdf_url}")
            print(f"      -> {save_folder}")
    else:
        print("\nAll previously failed PDFs were recovered on retry.")
def process_pdf_list(page, context, pdf_list, save_folder, section_label):
    print("\n\n")
    print("##################################################")
    print(f"# {section_label}")
    print("##################################################")
    for number, (href, text) in enumerate(pdf_list, start=1):
        filename = clean_filename(text)
        save_pdf_from_link(
            page,
            context,
            href,
            filename,
            save_folder,
            number,
            len(pdf_list),
            section_label=section_label
        )
def extract_circular_table_rows(page):
    """
    Reads every row of the DataTable currently visible on the
    page and returns a list of dicts: title, date, category,
    and the absolute PDF URL from the "Click To View" column's
    link (already an absolute cpcb.gov.in URL in the page's
    HTML, so no relative-URL resolution is needed for it).
    """
    try:
        rows = page.evaluate(
            """
            () => Array.from(
                document.querySelectorAll('#report tbody tr')
            ).map(tr => {
                const tds = tr.querySelectorAll('td');
                const titleTd = tds[2];
                const dateTd = tds[3];
                const categoryTd = tds[4];
                const viewTd = tds[5];
                const link = viewTd
                    ? viewTd.querySelector('a[href]')
                    : null;
                return {
                    title: titleTd ? titleTd.innerText.trim() : '',
                    date: dateTd ? dateTd.innerText.trim() : '',
                    category: categoryTd
                        ? categoryTd.innerText.trim()
                        : '',
                    href: link ? link.getAttribute('href') : ''
                };
            })
            """
        )
    except Exception as e:
        print(f"Could not read table rows: {e}")
        rows = []
    return rows
def go_to_next_table_page(page):
    """
    Clicks the DataTable's "Next" pagination button
    (#report_next) and waits for the table to refresh. Returns
    False when there's no next page (button missing or has the
    "disabled" class), True otherwise.
    """
    next_button = page.locator("#report_next")
    try:
        if next_button.count() == 0:
            return False
        classes = next_button.get_attribute("class") or ""
    except Exception:
        return False
    if "disabled" in classes:
        return False
    try:
        next_button.click()
    except Exception:
        return False
    page.wait_for_timeout(1500)
    return True
def truncate_to_byte_limit(text, max_bytes=180):
    """
    Keeps a filename-safe piece of text under a UTF-8 byte
    budget. Needed because some circular titles are long Hindi
    text, where each character can take multiple bytes — a
    plain character-count truncation isn't enough to stay under
    typical filesystem filename limits (255 bytes).
    """
    while len(text.encode("utf-8")) > max_bytes:
        text = text[:-1]
    return text
def build_circular_filename(row):
    title = row.get("title", "").strip()
    date = row.get("date", "").strip()
    raw = f"{date} - {title}" if date else title
    raw = truncate_to_byte_limit(raw, max_bytes=180)
    return clean_filename(raw)
def process_circular_page(page, context):
    print("\n\n")
    print("##################################################")
    print("# EMPLOYEE CORNER — OFFICE ORDER / CIRCULAR")
    print("# (Category = 'Circular' only, all pages)")
    print("##################################################")
    print(f"\nOpening: {CIRCULAR_PAGE_URL}")
    page.goto(
        CIRCULAR_PAGE_URL,
        wait_until="domcontentloaded",
        timeout=60000
    )
    page.wait_for_timeout(1500)
    page.bring_to_front()
    processed_count = 0
    table_page_number = 1
    max_table_pages = 20  # safety cap against an infinite loop
    while True:
        print(f"\n--- Reading table page {table_page_number} ---")
        rows = extract_circular_table_rows(page)
        circular_rows = [
            row for row in rows
            if row.get("category", "").strip().lower() == "circular"
            and row.get("href")
        ]
        print(
            f"{len(rows)} row(s) on this table page — "
            f"{len(circular_rows)} in the 'Circular' category."
        )
        for row in circular_rows:
            processed_count += 1
            filename = build_circular_filename(row)
            save_pdf_from_link(
                page,
                context,
                row["href"],
                filename,
                CIRCULARS_FOLDER,
                processed_count,
                14,  # expected total 'Circular' rows across all pages
                section_label="Circulars"
            )
        moved_to_next = go_to_next_table_page(page)
        if not moved_to_next:
            break
        table_page_number += 1
        if table_page_number > max_table_pages:
            print("Hit the table-page safety cap — stopping.")
            break
    print(
        f"\nDone — saved {processed_count} 'Circular' category "
        f"PDF(s) across {table_page_number} table page(s)."
    )
def main():
    print("\n")
    print("==================================================")
    print("CPCB — HAZARDOUS WASTE RULES PAGE AUTOMATION")
    print("==================================================")
    print(f"\nMain folder:\n{ROOT_FOLDER}")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--start-maximized"]
        )
        context = browser.new_context(
            no_viewport=True,
            accept_downloads=True
        )
        page = context.new_page()
        print(f"\nOpening: {PAGE_URL}")
        page.goto(
            PAGE_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )
        page.wait_for_timeout(1000)
        print("Page opened successfully.")
        process_pdf_list(
            page,
            context,
            OTHER_WASTE_PDFS,
            OTHER_WASTE_FOLDER,
            "Other waste (2016 Rules amendments)"
        )
        process_pdf_list(
            page,
            context,
            HAZARDOUS_WASTE_PDFS,
            HAZARDOUS_WASTE_FOLDER,
            "Hazardous waste (2008 Rules amendments)"
        )
        process_circular_page(page, context)
        retry_failed_downloads(context)
        print("\n")
        print("==================================================")
        print("ALL PROCESSING COMPLETED")
        print("==================================================")
        print("\nFOLDER STRUCTURE:\n")
        print("CPCB/")
        print("├── Hazardous waste Rules/")
        print("│   ├── Other waste/")
        print("│   └── Hazardous waste/")
        print("└── Circulars/")
        print("\nClosing Chrome...")
        browser.close()
        print("Chrome closed. All done.")
if __name__ == "__main__":
    main()
