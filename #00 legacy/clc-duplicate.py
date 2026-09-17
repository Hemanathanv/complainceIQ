import logging
import re
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

# Logger writes messages to both the terminal and automation.log.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("automation.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)
 

def run_bot():
    # Step 1: Website address and PDF title to search for
    website_url = "https://clc.gov.in/clc/circulars"
    wanted_title = "THE BUILDING AND OTHER CONSTRUCTION WORKERS"

    # Step 2: Create a folder named "downloads" if it does not exist
    download_folder = Path("downloads")
    download_folder.mkdir(exist_ok=True)

    # Step 3: Start Playwright and open Chrome
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()

        # try: Run the main program code.
        # If an unexpected error happens, Python moves to except below.
        try:
            # Step 4: Open the Circulars page directly.
            # Opening it directly is more reliable than clicking a hidden menu link.
            page.goto(website_url, wait_until="domcontentloaded", timeout=120000)

            # Step 5: Save the current page and pager page links
            page_urls = [page.url]
            pager_links = page.locator(".pager a[href]").evaluate_all(
                "links => links.map(link => link.href)"
            )

            for link in pager_links:
                if link not in page_urls:
                    page_urls.append(link)

            # Step 6: Check every Circulars page
            downloaded_count = 0

            for circular_page_url in page_urls:
                page.goto(
                    circular_page_url,
                    wait_until="domcontentloaded",
                    timeout=120000,
                )
                rows = page.locator("table.views-table tbody tr")

                # Step 7: Check each table row for the wanted title
                for row_number in range(rows.count()):
                    row = rows.nth(row_number)
                    title = row.locator("td.views-field-title").inner_text().strip()

                    # Ignore capital letters and symbols while comparing titles
                    clean_title = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
                    clean_wanted_title = re.sub(
                        r"[^a-z0-9]+", " ", wanted_title.lower()
                    ).strip()

                    if clean_wanted_title not in clean_title:
                        continue

                    # Step 8: Get the PDF link from the matching row
                    pdf_link = row.locator(
                        "td.views-field-field-file-circular a"
                    ).get_attribute("href")

                    if not pdf_link:
                        logger.warning("PDF link was not found for: %s", title)
                        continue

                    # Step 9: Try to download and save the PDF.
                    # If one PDF fails, except shows the error and the bot continues.
                    try:
                        pdf_url = urljoin(circular_page_url, pdf_link)
                        response = page.request.get(pdf_url, timeout=120000)

                        if not response.ok:
                            logger.error("Could not download: %s", title)
                            continue

                        # Windows file names cannot contain these characters
                        file_name = re.sub(r'[<>:"/\\|?*]+', "_", title)
                        pdf_path = download_folder / f"{file_name}.pdf"
                        pdf_path.write_bytes(response.body())

                        downloaded_count += 1
                        logger.info("Downloaded: %s", pdf_path)

                    except Exception as error:
                        logger.error("Could not download %s because: %s", title, error)

            # Step 10: Show final result
            logger.info("Finished. %d PDF(s) downloaded.", downloaded_count)

        # except: Runs only when an error happens inside the main try block.
        except Exception as error:
            logger.error("Something went wrong: %s", error)

        # finally: Always runs, whether the code succeeds or fails.
        finally:
            input("Press ENTER to close browser...")
            browser.close()


# Step 11: Start the bot when this file is run directly
if __name__ == "__main__":
    run_bot()
