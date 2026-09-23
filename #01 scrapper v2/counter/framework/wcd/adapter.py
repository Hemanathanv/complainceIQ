from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from ..models import AuditEvent, DocumentRecord
from ..runtime import BrowserRuntime, sha256_bytes, utc_now
from ..state import StateStore
from .config import LISTINGS, WCDListing


@dataclass
class WCDCollection:
    records: list[DocumentRecord]
    pages_checked: int
    raw_rows_seen: int
    rows_skipped: int
    transport: str
    complete: bool
    last_updated: str = ""
    error: str = ""


class WCDAdapter:
    """Monitor current WCD Orders & Notices and Notifications listings."""

    def __init__(self, state_path: Path, headless: bool = True):
        self.state = StateStore(state_path)
        self.headless = headless

    async def poll_once(self, *, max_pages: int = 1,
                        category_timeout: float = 180.0,
                        download_new: bool = False,
                        download_dir: Path | None = None,
                        download_existing: bool = False) -> dict:
        checked_at = utc_now()
        summary = {"source": "WCD", "categories": {}, "checked_at": checked_at}
        try:
            async with BrowserRuntime(headless=self.headless, channel="chrome") as runtime:
                for listing in LISTINGS:
                    result = await self._collect(runtime, listing, category_timeout)
                    await self._record_result(
                        summary, listing, result, checked_at, runtime,
                    download_new, download_dir,
                    download_existing,
                    )
        except Exception as error:
            for listing in LISTINGS:
                if listing.category not in summary["categories"]:
                    summary["categories"][listing.category] = {
                        "status": "failed", "records_checked": 0,
                        "new_records": 0, "reported_total": None,
                        "pages_checked": 0, "raw_rows_seen": 0,
                        "rows_skipped": 0, "complete": False,
                        "transport": "unavailable", "last_updated": "",
                        "documents_downloaded": 0, "document_errors": 0,
                        "error": str(error),
                    }
        return summary

    async def _collect(self, runtime: BrowserRuntime, listing: WCDListing,
                       timeout: float) -> WCDCollection:
        page = await runtime.new_page()
        try:
            response = await page.goto(listing.url, wait_until="domcontentloaded", timeout=90000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"HTTP {response.status} from {listing.url}")
            await self._wait_for_cards(page)
            records, skipped = await self._extract_cards(page, listing)
            return WCDCollection(
                records=records, pages_checked=1,
                raw_rows_seen=len(records) + skipped, rows_skipped=skipped,
                transport="playwright", complete=True,
                last_updated=await self._last_updated(page),
            )
        except Exception as error:
            return WCDCollection(
                records=[], pages_checked=1, raw_rows_seen=0,
                rows_skipped=0, transport="playwright", complete=False,
                error=str(error),
            )
        finally:
            await page.close()

    async def _extract_cards(self, page, listing: WCDListing):
        cards = page.locator(".list_det_bx:visible")
        records: list[DocumentRecord] = []
        skipped = 0
        for index in range(await cards.count()):
            card = cards.nth(index)
            title_locator = card.locator(".row .col-md-7 p").first
            date_locator = card.locator(".row .col-md-2 small").first
            links = card.locator('a[href*=".pdf"]')
            if not await title_locator.count() or not await date_locator.count() or not await links.count():
                skipped += 1
                continue
            title = " ".join((await title_locator.inner_text()).split())
            published_date = " ".join((await date_locator.inner_text()).split())
            document_urls: list[str] = []
            link_details: list[dict[str, str]] = []
            document_type = ""
            file_size = ""
            size_locator = card.locator(".viewdiv small").first
            if await size_locator.count():
                size_text = " ".join((await size_locator.inner_text()).split())
                file_size = re.sub(r"^PDF\s*", "", size_text, flags=re.I).strip()
                icon = size_locator.locator('img[alt*="PDF" i], img[src*="pdf" i]').first
                if await icon.count():
                    document_type = (await icon.get_attribute("alt") or "").strip()
            for link_index in range(await links.count()):
                link = links.nth(link_index)
                href = await link.get_attribute("href")
                if not href:
                    continue
                document_url = urljoin(listing.url, href)
                if not document_url.lower().startswith(("http://", "https://")):
                    continue
                if document_url not in document_urls:
                    document_urls.append(document_url)
                link_details.append({
                    "text": " ".join((await link.inner_text()).split()),
                    "url": document_url,
                })
            if not document_type:
                document_type = "PDF" if document_urls else ""
            elif "pdf" in document_type.lower():
                document_type = "PDF"
            identity = "|".join((listing.category, title, published_date,
                                 "|".join(document_urls)))
            fallback_url = (
                f"{listing.url}#record-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
            )
            detail_url = document_urls[0] if document_urls else fallback_url
            metadata = (
                ("published_date", published_date),
                ("document_type", document_type),
                ("file_size", file_size),
                ("document_links", json.dumps(link_details, ensure_ascii=False)),
            )
            records.append(DocumentRecord(
                source="WCD", category=listing.category,
                record_date=published_date, title=title, detail_url=detail_url,
                document_urls=tuple(document_urls), metadata=metadata,
            ))
        return records, skipped

    async def _wait_for_cards(self, page) -> None:
        for _ in range(100):
            if await page.locator(".list_det_bx:visible").count():
                return
            await page.wait_for_timeout(300)
        raise RuntimeError("WCD document cards did not load")

    async def _last_updated(self, page) -> str:
        text = " ".join((await page.locator("body").inner_text()).split())
        match = re.search(r"Last\s+updated\s+on\s*:\s*([0-9]{2}\.[0-9]{2}\.[0-9]{4})", text, re.I)
        return match.group(1) if match else ""

    async def _record_result(self, summary, listing, result, checked_at,
                             runtime, download_new, download_dir,
                             download_existing):
        new_count = downloaded = download_errors = 0
        for record in result.records:
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
        status = "ok" if not result.error else ("partial" if result.records else "failed")
        summary["categories"][listing.category] = {
            "status": status, "records_checked": len(result.records),
            "new_records": new_count, "reported_total": None,
            "pages_checked": result.pages_checked, "raw_rows_seen": result.raw_rows_seen,
            "rows_skipped": result.rows_skipped, "complete": result.complete,
            "last_updated": result.last_updated, "transport": result.transport,
            "documents_downloaded": downloaded, "document_errors": download_errors,
        }
        if result.error:
            summary["categories"][listing.category]["error"] = result.error

    async def _download(self, record, runtime, download_dir):
        folder = (download_dir or Path("data") / "wcd_documents") / re.sub(
            r"[^A-Za-z0-9._ -]+", "_", record.category
        )
        folder.mkdir(parents=True, exist_ok=True)
        downloaded = errors = 0
        for index, url in enumerate(record.document_urls, start=1):
            try:
                response = await runtime.context.request.get(
                    url, timeout=90000, headers={"Referer": "https://wcd.gov.in/"}
                )
                if not response.ok:
                    raise RuntimeError(f"PDF HTTP {response.status}")
                data = await response.body()
                if not data.startswith(b"%PDF"):
                    raise RuntimeError("response was not a PDF")
                filename = Path(url.split("?", 1)[0]).name or "document.pdf"
                filename = re.sub(r"[^A-Za-z0-9._ -]+", "_", filename)
                if not filename.lower().endswith(".pdf"):
                    filename += ".pdf"
                path = folder / f"{record.fingerprint[:10]}_{index:02d}_{filename}"
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
                    event="DOCUMENT_FAILED", occurred_at=utc_now(),
                    document_url=url, message=str(error),
                    metadata_json=json.dumps(dict(record.metadata), ensure_ascii=False),
                ))
                errors += 1
        return downloaded, errors

    def close(self):
        self.state.close()
