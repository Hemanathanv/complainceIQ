from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from urllib.parse import urljoin

from camoufox.async_api import AsyncCamoufox

from ..models import AuditEvent, DocumentRecord
from ..runtime import sha256_bytes, utc_now
from ..state import StateStore
from .config import CIRCULARS, EPFOListing


class StandaloneCamoufoxRuntime:
    """Fresh standalone browser runtime; no user Chrome session required."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._camoufox = None
        self.browser = None
        self.context = None

    async def __aenter__(self):
        self._camoufox = AsyncCamoufox(
            headless=self.headless,
            humanize=True,
            os="windows",
        )
        self.browser = await self._camoufox.__aenter__()
        self.context = await self.browser.new_context()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.context:
            await self.context.close()
        if self._camoufox:
            await self._camoufox.__aexit__(exc_type, exc, tb)

    async def new_page(self):
        return await self.context.new_page()


@dataclass
class EPFOCollection:
    records: list[DocumentRecord]
    reported_total: int | None
    pages_checked: int
    raw_rows_seen: int
    rows_skipped: int
    transport: str
    complete: bool
    error: str = ""


class EPFOAdapter:
    """Dynamic EPFO circular discovery and optional new-document download."""

    def __init__(self, state_path: Path, headless: bool = True):
        self.state = StateStore(state_path)
        self.headless = headless

    async def poll_once(self, *, max_pages: int = 1,
                        category_timeout: float = 180.0,
                        download_new: bool = False,
                        download_dir: Path | None = None,
                        download_existing: bool = False) -> dict:
        checked_at = utc_now()
        summary = {"source": "EPFO", "categories": {}, "checked_at": checked_at}
        try:
            async with StandaloneCamoufoxRuntime(headless=self.headless) as runtime:
                result = await self._collect(runtime, CIRCULARS, max_pages, category_timeout)
                await self._record_result(
                    summary, CIRCULARS, result, checked_at, runtime,
                    download_new, download_dir,
                    download_existing,
                )
        except Exception as error:
            summary["categories"][CIRCULARS.category] = {
                "status": "failed", "records_checked": 0,
                "new_records": 0, "reported_total": None,
                "pages_checked": 0, "raw_rows_seen": 0, "rows_skipped": 0,
                "complete": False, "transport": "unavailable",
                "documents_downloaded": 0, "document_errors": 0,
                "error": str(error),
            }
        return summary

    async def _collect(self, runtime: BrowserRuntime, listing: EPFOListing,
                       max_pages: int, timeout: float) -> EPFOCollection:
        page = await runtime.new_page()
        records: list[DocumentRecord] = []
        seen: set[str] = set()
        visited: set[tuple[str, ...]] = set()
        reported_total: int | None = None
        pages_checked = raw_rows = rows_skipped = 0
        started = monotonic()
        error = ""
        try:
            response = await page.goto(listing.url, wait_until="domcontentloaded", timeout=90000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"HTTP {response.status} from {listing.url}")
            await self._wait_for_rows(page)
            while pages_checked < max_pages:
                if monotonic() - started >= timeout:
                    error = "category timeout reached"
                    break
                rows, total, skipped = await self._extract_rows(page, listing)
                if total is not None:
                    reported_total = total
                pages_checked += 1
                raw_rows += len(rows) + skipped
                rows_skipped += skipped
                signature = tuple(
                    f"{row.detail_url}|{row.record_date}|{row.title}" for row in rows
                )
                if signature in visited:
                    error = "pagination returned a duplicate page"
                    break
                visited.add(signature)
                for record in rows:
                    if record.detail_url not in seen:
                        seen.add(record.detail_url)
                        records.append(record)
                if reported_total is not None and len(records) >= reported_total:
                    break
                if pages_checked >= max_pages:
                    break
                remaining = timeout - (monotonic() - started)
                if not await self._advance_page(page, signature, remaining):
                    break
            complete = not error and (
                reported_total is None or len(records) >= reported_total
            )
            return EPFOCollection(
                records, reported_total, pages_checked, raw_rows,
                rows_skipped, "playwright", complete, error,
            )
        except Exception as exc:
            return EPFOCollection(
                records, reported_total, pages_checked, raw_rows,
                rows_skipped, "playwright", False, str(exc),
            )
        finally:
            await page.close()

    async def _extract_rows(self, page, listing: EPFOListing):
        # The current EPFO page renders the complete result set in the DOM.
        # Extract it in one browser-side pass instead of making thousands of
        # round trips through Playwright.
        rows_data = await page.locator("div.download-box").evaluate_all(
            """boxes => boxes.map(box => ({
                serial: box.querySelector('.serial-num')?.innerText?.trim() || '',
                title: box.querySelector('.download-title')?.innerText?.trim() || '',
                values: Array.from(box.querySelectorAll('.meta-row .meta-item'))
                    .map(item => item.innerText.trim()),
                urls: Array.from(box.querySelectorAll('a[href]'))
                    .map(link => link.getAttribute('href') || ''),
                raw_text: box.innerText.trim(),
            }))"""
        )
        total = await self._reported_total(page)
        records: list[DocumentRecord] = []
        skipped = 0
        for row in rows_data:
            title = " ".join((row.get("title") or "").split())
            values = [" ".join(value.split()) for value in row.get("values", [])]
            if not title:
                skipped += 1
                continue
            raw_text = " ".join((row.get("raw_text") or "").split())
            division = values[0] if values else ""
            record_date = values[-1] if len(values) > 1 else ""
            if not record_date:
                date_match = re.search(r"\b\d{2}[/-]\d{2}[/-]\d{4}\b", title)
                record_date = date_match.group(0) if date_match else ""
            s_no = " ".join((row.get("serial") or "").split()).rstrip(".")
            s_no = s_no or self._labeled_value(raw_text, r"(?:s\.?\s*no\.?|serial\s*no\.?)")
            circular_no = self._labeled_value(
                raw_text, r"(?:circular|instruction|instn\.?|o\.?\s*o\.?)\s*(?:no\.?)?"
            )
            dated = self._labeled_value(raw_text, r"dated") or record_date
            publish_date = self._labeled_value(raw_text, r"publish(?:ed)?\s*date")
            console_no = self._labeled_value(raw_text, r"console(?:\s+sl\.?)?\s*no\.?")
            document_urls = []
            for href in row.get("urls", []):
                if href and href.lower() != "http://false":
                    url = urljoin(listing.url, href)
                    if url.lower().startswith(("http://", "https://")) and url not in document_urls:
                        document_urls.append(url)
            stable_url = f"{listing.url}#record-{self._identity(title, record_date, division, s_no)}"
            records.append(DocumentRecord(
                source="EPFO", category=listing.category,
                record_date=record_date, title=title, detail_url=stable_url,
                document_urls=tuple(document_urls),
                metadata=(
                    ("s_no", s_no), ("branch", division),
                    ("circular_instruction_oo_no", circular_no),
                    ("dated", dated), ("subject", title),
                    ("publish_date", publish_date), ("console_sl_no", console_no),
                    ("division", division), ("record_date", record_date),
                    ("meta_values", json.dumps(values, ensure_ascii=False)),
                    ("raw_row_text", raw_text),
                ),
            ))
        if total is None:
            serials = [
                int(str(row.get("serial", "")).rstrip("."))
                for row in rows_data
                if str(row.get("serial", "")).rstrip(".").isdigit()
            ]
            total = max(serials) if serials else None
        return records, total, skipped

    @staticmethod
    def _labeled_value(text: str, label_pattern: str) -> str:
        """Read a labeled field without losing the original row text."""
        match = re.search(
            rf"{label_pattern}\s*[:\-]?\s*([^|]+?)(?=\s+(?:S\.?\s*No\.?|Branch|Subject|Publish|Console|Dated|$))",
            text, re.I,
        )
        return " ".join(match.group(1).split()) if match else ""

    @staticmethod
    def _identity(title: str, record_date: str, division: str, s_no: str = "") -> str:
        import hashlib
        value = "|".join((s_no, title, record_date, division))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    async def _reported_total(self, page) -> int | None:
        text = " ".join((await page.locator("body").inner_text()).split())
        match = re.search(
            r"Showing\s+\d+\s*-\s*\d+\s+of\s+([\d,]+)\s+circulars",
            text, re.I,
        )
        return int(match.group(1).replace(",", "")) if match else None

    async def _wait_for_rows(self, page) -> None:
        for _ in range(60):
            if await page.locator("div.download-box:visible").count():
                return
            await page.wait_for_timeout(300)
        raise RuntimeError("EPFO circular rows did not load")

    async def _page_signature(self, page) -> tuple[str, ...]:
        rows, _, _ = await self._extract_rows(page, CIRCULARS)
        return tuple(f"{row.title}|{row.record_date}|{row.detail_url}" for row in rows)

    async def _advance_page(self, page, before: tuple[str, ...], timeout: float) -> bool:
        if timeout <= 0:
            return False
        next_button = page.locator("button.next-btn").first
        if (
            not await next_button.count()
            or not await next_button.is_visible()
            or await next_button.is_disabled()
        ):
            return False
        await next_button.click(timeout=min(20000, int(timeout * 1000)), force=True)
        for _ in range(60):
            await page.wait_for_timeout(300)
            current = await self._page_signature(page)
            if current and current != before:
                return True
        return False

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
            "rows_skipped": result.rows_skipped,
            "complete": result.complete and not result.error,
            "transport": result.transport, "documents_downloaded": downloaded,
            "document_errors": download_errors,
        }
        if result.error:
            summary["categories"][listing.category]["error"] = result.error

    async def _download(self, record, runtime, download_dir):
        if not record.document_urls:
            self.state.audit(AuditEvent(
                fingerprint=record.fingerprint, source=record.source,
                category=record.category, record_date=record.record_date,
                title=record.title, detail_url=record.detail_url,
                event="DOCUMENT_FAILED", occurred_at=utc_now(),
                message="listing row has no usable PDF URL",
                metadata_json=json.dumps(dict(record.metadata), ensure_ascii=False),
            ))
            return 0, 1
        url = record.document_urls[0]
        try:
            response = await runtime.context.request.get(url, timeout=90000)
            if not response.ok:
                raise RuntimeError(f"PDF HTTP {response.status}")
            data = await response.body()
            if not data.startswith(b"%PDF"):
                raise RuntimeError("response was not a PDF")
            folder = (download_dir or Path("data") / "epfo_documents") / "Circular"
            folder.mkdir(parents=True, exist_ok=True)
            filename = re.sub(r"[^A-Za-z0-9._ -]+", "_", record.title)[:170].strip() or "EPFO_circular"
            path = folder / f"{filename}_{record.fingerprint[:10]}.pdf"
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
            return 1, 0
        except Exception as error:
            self.state.audit(AuditEvent(
                fingerprint=record.fingerprint, source=record.source,
                category=record.category, record_date=record.record_date,
                title=record.title, detail_url=record.detail_url,
                event="DOCUMENT_FAILED", occurred_at=utc_now(),
                document_url=url, message=str(error),
                metadata_json=json.dumps(dict(record.metadata), ensure_ascii=False),
            ))
            return 0, 1

    def close(self):
        self.state.close()
