from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.parse import urljoin

from ..models import AuditEvent, DocumentRecord
from ..runtime import BrowserRuntime, sha256_bytes, utc_now
from ..state import StateStore
from .config import CIRCULARS, CPCBListing


@dataclass
class CPCBCollection:
    records: list[DocumentRecord]
    reported_total: int | None
    pages_checked: int
    raw_rows_seen: int
    rows_skipped: int
    transport: str
    complete: bool
    updated_on: str = ""
    error: str = ""


class CPCBAdapter:
    """Monitor CPCB's current Circular/Office Order/Memorandum table."""

    def __init__(self, state_path: Path, headless: bool = True):
        self.state = StateStore(state_path)
        self.headless = headless

    async def poll_once(self, *, max_pages: int = 1,
                        category_timeout: float = 180.0,
                        download_new: bool = False,
                        download_dir: Path | None = None,
                        download_existing: bool = False) -> dict:
        checked_at = utc_now()
        summary = {"source": "CPCB", "categories": {}, "checked_at": checked_at}
        try:
            async with BrowserRuntime(headless=self.headless, channel="chrome") as runtime:
                result = await self._collect(
                    runtime, CIRCULARS, all_rows=max_pages > 1,
                    timeout=category_timeout,
                )
                await self._record_result(
                    summary, CIRCULARS, result, checked_at, runtime,
                    download_new, download_dir,
                    download_existing,
                )
        except Exception as error:
            summary["categories"][CIRCULARS.category] = {
                "status": "failed", "records_checked": 0, "new_records": 0,
                "reported_total": None, "pages_checked": 0,
                "raw_rows_seen": 0, "rows_skipped": 0, "complete": False,
                "updated_on": "", "transport": "unavailable",
                "documents_downloaded": 0, "document_errors": 0,
                "error": str(error),
            }
        return summary

    async def _collect(self, runtime: BrowserRuntime, listing: CPCBListing,
                       all_rows: bool, timeout: float) -> CPCBCollection:
        page = await runtime.new_page()
        try:
            response = await page.goto(listing.url, wait_until="domcontentloaded", timeout=90000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"HTTP {response.status} from {listing.url}")
            await self._wait_for_table(page)
            reported_total = await self._reported_total(page)
            if all_rows:
                await page.locator("select").first.select_option(label="All")
                await page.wait_for_timeout(500)
            records, skipped = await self._extract_rows(page, listing)
            if all_rows and reported_total is not None and len(records) != reported_total:
                raise RuntimeError(
                    f"CPCB table showed {len(records)} rows after All, expected {reported_total}"
                )
            return CPCBCollection(
                records=records, reported_total=reported_total,
                pages_checked=1, raw_rows_seen=len(records) + skipped,
                rows_skipped=skipped, transport="playwright",
                complete=(reported_total is None or len(records) >= reported_total),
                updated_on=await self._updated_on(page),
            )
        except Exception as error:
            return CPCBCollection(
                records=[], reported_total=None, pages_checked=1,
                raw_rows_seen=0, rows_skipped=0, transport="playwright",
                complete=False, error=str(error),
            )
        finally:
            await page.close()

    async def _extract_rows(self, page, listing: CPCBListing):
        table = page.locator("table").nth(2)
        rows = table.locator("tbody tr:visible")
        records: list[DocumentRecord] = []
        skipped = 0
        for index in range(await rows.count()):
            cells = rows.nth(index).locator("td")
            if await cells.count() < 6:
                skipped += 1
                continue
            values = [" ".join((await cells.nth(i).inner_text()).split()) for i in range(6)]
            s_no, division, title, issue_date, category, click_to_view = values
            links = cells.nth(5).locator("a[href]")
            document_urls: list[str] = []
            for link_index in range(await links.count()):
                href = await links.nth(link_index).get_attribute("href")
                if not href:
                    continue
                url = urljoin(listing.url, href)
                if url.startswith(("http://", "https://")) and url not in document_urls:
                    document_urls.append(url)
            if not title or not issue_date:
                skipped += 1
                continue
            identity = "|".join((s_no, division, title, issue_date, category,
                                 "|".join(document_urls)))
            fallback = f"{listing.url}#record-{sha256(identity.encode('utf-8')).hexdigest()[:24]}"
            detail_url = document_urls[0] if document_urls else fallback
            records.append(DocumentRecord(
                source="CPCB", category=listing.category,
                record_date=issue_date, title=title, detail_url=detail_url,
                document_urls=tuple(document_urls),
                metadata=(
                    ("s_no", s_no), ("division", division),
                    ("date_of_issue", issue_date), ("row_category", category),
                    ("document_type", "PDF" if document_urls else ""),
                    ("file_size", click_to_view),
                ),
            ))
        return records, skipped

    async def _wait_for_table(self, page) -> None:
        for _ in range(100):
            if await page.locator("table").nth(2).locator("tbody tr").count():
                return
            await page.wait_for_timeout(300)
        raise RuntimeError("CPCB circular table did not load")

    async def _reported_total(self, page) -> int | None:
        text = " ".join((await page.locator("body").inner_text()).split())
        match = re.search(r"Showing\s+\d+\s+to\s+\d+\s+of\s+([\d,]+)\s+entries", text, re.I)
        return int(match.group(1).replace(",", "")) if match else None

    async def _updated_on(self, page) -> str:
        text = " ".join((await page.locator("body").inner_text()).split())
        match = re.search(r"Updated\s+On\s*:\s*([^ ]+\s+[^ ]+\s+\d{4})", text, re.I)
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
            "new_records": new_count, "reported_total": result.reported_total,
            "pages_checked": result.pages_checked, "raw_rows_seen": result.raw_rows_seen,
            "rows_skipped": result.rows_skipped, "complete": result.complete,
            "updated_on": result.updated_on, "transport": result.transport,
            "documents_downloaded": downloaded, "document_errors": download_errors,
        }
        if result.error:
            summary["categories"][listing.category]["error"] = result.error

    async def _download(self, record, runtime, download_dir):
        folder = (download_dir or Path("data") / "cpcb_documents") / "Circulars"
        folder.mkdir(parents=True, exist_ok=True)
        downloaded = errors = 0
        for index, url in enumerate(record.document_urls, start=1):
            try:
                response = await runtime.context.request.get(
                    url, timeout=90000, headers={"Referer": "https://cpcb.gov.in/circular/"}
                )
                if not response.ok:
                    raise RuntimeError(f"PDF HTTP {response.status}")
                data = await response.body()
                # CPCB wraps the PDF bytes in a small HTML document while
                # still declaring application/pdf. Strip that wrapper before
                # validating and saving the document.
                pdf_start = data.find(b"%PDF")
                pdf_end = data.rfind(b"%%EOF")
                if pdf_start >= 0 and pdf_end >= pdf_start:
                    data = data[pdf_start:pdf_end + len(b"%%EOF")]
                if not data.startswith(b"%PDF"):
                    raise RuntimeError("response was not a PDF")
                base = re.sub(r"[^A-Za-z0-9._ -]+", "_", record.title)[:150].strip() or "CPCB_circular"
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
