from __future__ import annotations

import json
import random
import re
import time
import asyncio
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from ..models import AuditEvent, DocumentRecord
from ..runtime import BrowserRuntime, sha256_bytes, utc_now
from ..state import StateStore
from .config import LISTINGS, LabourListing


@dataclass
class LabourCollection:
    records: list[DocumentRecord]
    reported_total: int | None
    pages_checked: int
    raw_rows_seen: int
    rows_skipped: int
    transport: str
    complete: bool
    last_updated: str = ""
    error: str = ""
    blocked: bool = False


class LabourAccessBlocked(RuntimeError):
    """The portal has refused automated access; stop the run safely."""

    def __init__(self, url: str, status: int):
        super().__init__(f"HTTP {status} from {url}; stopping to avoid escalating the block")
        self.url = url
        self.status = status


class LabourAdapter:
    """Monitor current Labour Orders/Notices and Gazette Notifications."""

    def __init__(self, state_path: Path, headless: bool = True,
                 request_delay: float = 6.0, request_jitter: float = 2.0):
        self.state = StateStore(state_path)
        self.headless = headless
        self.request_delay = max(0.0, request_delay)
        self.request_jitter = max(0.0, request_jitter)
        self._last_navigation = 0.0
        self._english_initialized = False

    async def poll_once(self, *, max_pages: int = 1,
                        category_timeout: float = 180.0,
                        download_new: bool = False,
                        download_dir: Path | None = None,
                        download_existing: bool = False,
                        listings: tuple[LabourListing, ...] | None = None) -> dict:
        active_listings = listings or LISTINGS
        checked_at = utc_now()
        summary = {"source": "Ministry of Labour & Employment",
                   "categories": {}, "checked_at": checked_at}
        try:
            async with BrowserRuntime(headless=self.headless, channel="chrome") as runtime:
                for listing in active_listings:
                    result = await self._collect_section(
                        runtime, listing, max_pages=max_pages,
                        timeout=category_timeout,
                    )
                    await self._record_result(
                        summary, listing, result, checked_at, runtime,
                        download_new, download_dir,
                        download_existing,
                    )
                    if result.blocked:
                        # Do not make another request after the portal returns
                        # 403/429. The remaining section is reported as blocked.
                        for remaining in active_listings:
                            if remaining.category not in summary["categories"]:
                                summary["categories"][remaining.category] = {
                                    "status": "blocked", "records_checked": 0,
                                    "new_records": 0, "reported_total": None,
                                    "pages_checked": 0, "raw_rows_seen": 0,
                                    "rows_skipped": 0, "complete": False,
                                    "last_updated": "", "transport": "playwright",
                                    "documents_downloaded": 0, "document_errors": 0,
                                    "error": "not attempted after portal block",
                                }
                        break
        except Exception as error:
            for listing in active_listings:
                if listing.category not in summary["categories"]:
                    summary["categories"][listing.category] = {
                        "status": "failed", "records_checked": 0,
                        "new_records": 0, "reported_total": None,
                        "pages_checked": 0, "raw_rows_seen": 0,
                        "rows_skipped": 0, "complete": False,
                        "last_updated": "", "transport": "unavailable",
                        "documents_downloaded": 0, "document_errors": 0,
                        "error": str(error),
                    }
        return summary

    async def _collect_section(self, runtime: BrowserRuntime,
                               listing: LabourListing, *, max_pages: int,
                               timeout: float) -> LabourCollection:
        page = await runtime.new_page()
        records: list[DocumentRecord] = []
        seen: set[str] = set()
        category_links: dict[str, tuple[str, int | None]] = {}
        pages_checked = raw_rows = rows_skipped = 0
        updated = ""
        started = time.monotonic()
        deep = max_pages > 1
        section_errors: list[str] = []
        try:
            for page_number in range(1, max(1, min(max_pages, 10)) + 1):
                if time.monotonic() - started >= timeout:
                    break
                response = await self._goto(
                    page,
                    self._page_url(listing.url, page_number),
                )
                await self._select_english(page)
                await self._wait_for_rows(page)
                page_records, page_categories, skipped = await self._extract_main_rows(
                    page, listing
                )
                pages_checked += 1
                raw_rows += len(page_records) + len(page_categories) + skipped
                rows_skipped += skipped
                for record in page_records:
                    if record.detail_url not in seen:
                        seen.add(record.detail_url)
                        records.append(record)
                category_links.update(page_categories)
                updated = await self._last_updated(page) or updated
                if not await self._has_page(page, page_number + 1):
                    break

            # Monitor mode checks the current section page only. The
            # category groups are reconciled during baseline mode, avoiding
            # a burst of requests to every group every five minutes.
            if deep:
                for category_title, (category_url, expected) in category_links.items():
                    if time.monotonic() - started >= timeout:
                        break
                    try:
                        category_records, category_pages, category_rows, category_skipped, category_updated = await self._collect_category(
                            runtime, listing, category_title, category_url,
                            expected, deep, timeout - (time.monotonic() - started),
                        )
                        pages_checked += category_pages
                        raw_rows += category_rows
                        rows_skipped += category_skipped
                        updated = category_updated or updated
                        for record in category_records:
                            if record.detail_url not in seen:
                                seen.add(record.detail_url)
                                records.append(record)
                    except Exception as error:
                        section_errors.append(f"{category_title}: {error}")

            # The portal gives category counts rather than one section total.
            # The collector's count is therefore the authoritative discovered
            # count for this run, never a hardcoded site total.
            complete = not deep or (not section_errors and all(
                expected is None or expected <= len({r.detail_url for r in records
                                                     if dict(r.metadata).get("section") == listing.category})
                for _, expected in category_links.values()
            ))
            return LabourCollection(
                records=records, reported_total=len(records),
                pages_checked=pages_checked, raw_rows_seen=raw_rows,
                rows_skipped=rows_skipped, transport="playwright",
                complete=complete, last_updated=updated,
                error="; ".join(section_errors),
            )
        except LabourAccessBlocked as error:
            return LabourCollection(
                records=records, reported_total=len(records) if records else None,
                pages_checked=pages_checked, raw_rows_seen=raw_rows,
                rows_skipped=rows_skipped, transport="playwright",
                complete=False, last_updated=updated, error=str(error), blocked=True,
            )
        except Exception as error:
            return LabourCollection(
                records=records, reported_total=len(records) if records else None,
                pages_checked=pages_checked, raw_rows_seen=raw_rows,
                rows_skipped=rows_skipped, transport="playwright",
                complete=False, last_updated=updated, error=str(error), blocked=False,
            )
        finally:
            await page.close()

    async def _extract_main_rows(self, page, listing):
        rows = page.locator('[role="row"].announcementbox:visible')
        records: list[DocumentRecord] = []
        categories: dict[str, tuple[str, int | None]] = {}
        skipped = 0
        for index in range(await rows.count()):
            row = rows.nth(index)
            title_locator = row.locator(".col-lg-7 p").first
            if not await title_locator.count():
                skipped += 1
                continue
            title = self._text(await title_locator.inner_text())
            counter = row.locator(".counter-box").first
            view_link = row.locator("a.download-btn").last
            href = await view_link.get_attribute("href") if await view_link.count() else ""
            if await counter.count() and href and "/documents/" in href:
                count_text = self._text(await counter.inner_text())
                categories[title] = (urljoin(page.url, href),
                                     int(count_text) if count_text.isdigit() else None)
                continue
            record = await self._row_to_record(row, listing.category, title,
                                               listing.url, page.url)
            if record is None:
                skipped += 1
            else:
                records.append(record)
        return records, categories, skipped

    async def _collect_category(self, runtime, listing, title, url, expected,
                                deep, timeout):
        page = await runtime.new_page()
        records: list[DocumentRecord] = []
        seen: set[str] = set()
        pages = raw = skipped = 0
        updated = ""
        started = time.monotonic()
        try:
            page_limit = 10 if deep else 1
            for page_number in range(1, page_limit + 1):
                if time.monotonic() - started >= timeout:
                    break
                page_url = url if page_number == 1 else self._page_url(url, page_number)
                await self._goto(page, page_url)
                await self._select_english(page)
                await self._wait_for_rows(page)
                if deep:
                    await self._select_largest_page(page)
                    await self._select_english(page)
                    await self._wait_for_rows(page)
                rows = page.locator('[role="row"].announcementbox:visible')
                page_count = 0
                for index in range(await rows.count()):
                    record = await self._row_to_record(
                        rows.nth(index), listing.category, title, url, page.url
                    )
                    if record is None:
                        skipped += 1
                        continue
                    page_count += 1
                    if record.detail_url not in seen:
                        seen.add(record.detail_url)
                        records.append(record)
                pages += 1
                raw += page_count
                updated = await self._last_updated(page) or updated
                if not deep or (expected is not None and len(records) >= expected):
                    break
                if not await self._has_page(page, page_number + 1):
                    break
            return records, pages, raw, skipped, updated
        except LabourAccessBlocked:
            raise
        finally:
            await page.close()

    async def _goto(self, page, url: str):
        """Navigate with a modest interval and stop immediately on a block."""
        elapsed = time.monotonic() - self._last_navigation
        wait_for = self.request_delay - elapsed
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        response = await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        self._last_navigation = time.monotonic()
        if response is not None and response.status in (403, 429):
            raise LabourAccessBlocked(url, response.status)
        if response is not None and response.status >= 400:
            raise RuntimeError(f"HTTP {response.status} from {url}")
        if self.request_jitter:
            await asyncio.sleep(random.uniform(0.0, min(self.request_jitter, 2.0)))
        return response

    async def _row_to_record(self, row, section, subcategory, base_url, page_url):
        title_locator = row.locator(".col-lg-7 p").first
        if not await title_locator.count():
            return None
        title = self._text(await title_locator.inner_text())
        date_locator = row.locator(".col-lg-2 .ptype").first
        published_date = self._text(await date_locator.inner_text()) if await date_locator.count() else ""
        size_locator = row.locator('[aria-label*="PDF size" i], .type-size small').first
        file_size = ""
        if await size_locator.count():
            file_size = self._text(await size_locator.inner_text())
            aria = await size_locator.get_attribute("aria-label") or ""
            match = re.search(r"PDF\s+size\s+(.+)", aria, re.I)
            if match:
                file_size = match.group(1).strip()
        links = row.locator('a[type="pdf"], a[href*=".pdf"]')
        document_urls = []
        for index in range(await links.count()):
            href = await links.nth(index).get_attribute("href")
            if href:
                url = urljoin(page_url or base_url, href)
                if url.startswith(("http://", "https://")) and url not in document_urls:
                    document_urls.append(url)
        if not document_urls:
            visit = row.locator("a.download-btn").first
            href = await visit.get_attribute("href") if await visit.count() else ""
            if href and href.startswith(("http://", "https://")):
                document_urls.append(href)
        identity = "|".join((section, subcategory, title, published_date,
                             "|".join(document_urls)))
        fallback = f"{base_url}#record-{sha256(identity.encode('utf-8')).hexdigest()[:24]}"
        is_visit = bool(document_urls) and not any(
            ".pdf" in url.lower() for url in document_urls
        )
        return DocumentRecord(
            source="Ministry of Labour & Employment", category=subcategory,
            record_date=published_date, title=title,
            # External e-Gazette Visit links are session-scoped and may be
            # identical for multiple rows. Keep the URL, but use the row
            # identity for deduplication.
            detail_url=fallback if is_visit else (document_urls[0] if document_urls else fallback),
            document_urls=tuple(document_urls),
            metadata=(
                ("section", section), ("subcategory", subcategory),
                ("published_date", published_date),
                ("document_type", "PDF" if any(".pdf" in url.lower() for url in document_urls) else "Visit" if document_urls else ""),
                ("file_size", file_size), ("source_page", page_url),
            ),
        )

    async def _select_english(self, page):
        if self._english_initialized:
            return
        await page.wait_for_timeout(700)
        for _ in range(3):
            button = page.locator("#bhashini-translation button").first
            option = page.locator('#bhashiniLanguageDropdown li[data-value="en"]').first
            if await button.count() and await button.is_visible():
                await button.click()
                if await option.count():
                    await option.wait_for(state="visible", timeout=3000)
                    await option.click(force=True)
                    await page.wait_for_timeout(900)
                    self._english_initialized = True
                    return
            await page.wait_for_timeout(700)
        # The current Labour pages are English by default. Mark the handshake
        # complete even when the optional translation control is absent, so a
        # missing control never causes repeated clicks on every page.
        self._english_initialized = True

    async def _select_largest_page(self, page):
        selects = page.locator("select")
        for index in range(await selects.count()):
            select = selects.nth(index)
            options = select.locator("option")
            for option_index in range(await options.count()):
                option = options.nth(option_index)
                label = self._text(await option.inner_text())
                if label.startswith("50"):
                    await select.select_option(label=label)
                    await page.wait_for_timeout(700)
                    return

    async def _wait_for_rows(self, page):
        for _ in range(100):
            if await page.locator('[role="row"].announcementbox:visible').count():
                return
            await page.wait_for_timeout(300)
        raise RuntimeError("Labour document rows did not load")

    async def _has_page(self, page, page_number):
        # The portal exposes an ARIA pagination navigation. Using its numbered
        # links avoids depending on whether the href is absolute, relative,
        # or query-only in a particular page template.
        pagination = page.get_by_role("navigation", name=re.compile("pagination", re.I))
        if await pagination.count():
            return await pagination.get_by_role(
                "link", name=str(page_number), exact=True
            ).count() > 0
        return False

    async def _last_updated(self, page):
        text = " ".join((await page.locator("body").inner_text()).split())
        match = re.search(r"Last\s+Updated\s+On\s*:\s*([0-9.]+)", text, re.I)
        return match.group(1) if match else ""

    @staticmethod
    def _page_url(url, page_number):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        query["page"] = [str(page_number)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    @staticmethod
    def _text(value):
        return " ".join((value or "").split())

    async def _record_result(self, summary, listing, result, checked_at,
                             runtime, download_new, download_dir,
                             download_existing):
        new_count = downloaded = download_errors = 0
        for record in result.records:
            if dict(record.metadata).get("document_type") == "Visit":
                self.state.rekey_matching_record(record, checked_at)
            is_new = self.state.upsert(record, checked_at)
            metadata_json = json.dumps(dict(record.metadata), ensure_ascii=False)
            self.state.audit(AuditEvent(
                fingerprint=record.fingerprint, source=record.source,
                category=record.category, record_date=record.record_date,
                title=record.title, detail_url=record.detail_url,
                event="NEW_RECORD" if is_new else "SEEN_RECORD",
                occurred_at=checked_at, transport=result.transport,
                metadata_json=metadata_json,
            ))
            new_count += int(is_new)
            if (is_new or download_existing) and download_new:
                count, errors = await self._download(record, runtime, download_dir)
                downloaded += count
                download_errors += errors
        status = ("blocked" if result.blocked else
                  ("ok" if not result.error else ("partial" if result.records else "failed")))
        summary["categories"][listing.category] = {
            "status": status, "records_checked": len(result.records),
            "new_records": new_count, "reported_total": result.reported_total,
            "pages_checked": result.pages_checked, "raw_rows_seen": result.raw_rows_seen,
            "rows_skipped": result.rows_skipped, "complete": result.complete,
            "last_updated": result.last_updated, "transport": result.transport,
            "documents_downloaded": downloaded, "document_errors": download_errors,
        }
        if result.error:
            summary["categories"][listing.category]["error"] = result.error

    async def _download(self, record, runtime, download_dir):
        pdf_urls = [url for url in record.document_urls if ".pdf" in url.lower()]
        if not pdf_urls:
            return 0, 0
        folder = (download_dir or Path("data") / "labour_documents") / re.sub(
            r"[^A-Za-z0-9._ -]+", "_", record.category
        )
        folder.mkdir(parents=True, exist_ok=True)
        downloaded = errors = 0
        for index, url in enumerate(pdf_urls, start=1):
            try:
                response = await runtime.context.request.get(url, timeout=90000,
                    headers={"Referer": "https://labour.gov.in/"})
                if not response.ok:
                    raise RuntimeError(f"PDF HTTP {response.status}")
                data = await response.body()
                if not data.startswith(b"%PDF"):
                    raise RuntimeError("response was not a PDF")
                base = re.sub(r"[^A-Za-z0-9._ -]+", "_", record.title)[:150].strip() or "labour_document"
                path = folder / f"{base}_{record.fingerprint[:10]}_{index:02d}.pdf"
                path.write_bytes(data)
                self.state.audit(AuditEvent(
                    fingerprint=record.fingerprint, source=record.source,
                    category=record.category, record_date=record.record_date,
                    title=record.title, detail_url=record.detail_url,
                    event="DOCUMENT_DOWNLOADED", occurred_at=utc_now(),
                    document_url=url, file_path=str(path.resolve()),
                    file_sha256=sha256_bytes(data), transport="playwright-request",
                    metadata_json=json.dumps(dict(record.metadata), ensure_ascii=False),
                ))
                downloaded += 1
            except Exception as error:
                self.state.audit(AuditEvent(
                    fingerprint=record.fingerprint, source=record.source,
                    category=record.category, record_date=record.record_date,
                    title=record.title, detail_url=record.detail_url,
                    event="DOCUMENT_FAILED", occurred_at=utc_now(), document_url=url,
                    message=str(error), metadata_json=json.dumps(dict(record.metadata), ensure_ascii=False),
                ))
                errors += 1
        return downloaded, errors

    def close(self):
        self.state.close()
