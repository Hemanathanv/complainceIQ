from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from urllib.parse import urljoin

from ..models import AuditEvent, DocumentRecord
from ..runtime import BrowserRuntime, sha256_bytes, utc_now
from ..state import StateStore
from .config import CIRCULARS, ESICListing


@dataclass
class ESICCollection:
    records: list[DocumentRecord]
    reported_total: int | None
    pages_checked: int
    raw_rows_seen: int
    rows_skipped: int
    transport: str
    complete: bool
    last_updated: str = ""
    error: str = ""


class ESICAdapter:
    """Discover ESIC circular rows and all PDF links attached to each row."""

    def __init__(self, state_path: Path, headless: bool = True):
        self.state = StateStore(state_path)
        self.headless = headless

    async def poll_once(self, *, max_pages: int = 1,
                        category_timeout: float = 180.0,
                        download_new: bool = False,
                        download_dir: Path | None = None,
                        download_existing: bool = False) -> dict:
        checked_at = utc_now()
        summary = {"source": "ESIC", "categories": {}, "checked_at": checked_at}
        try:
            async with BrowserRuntime(headless=self.headless, channel="chrome") as runtime:
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
                "last_updated": "", "documents_downloaded": 0,
                "document_errors": 0, "error": str(error),
            }
        return summary

    async def _collect(self, runtime: BrowserRuntime, listing: ESICListing,
                       max_pages: int, timeout: float) -> ESICCollection:
        page = await runtime.new_page()
        records: list[DocumentRecord] = []
        seen: set[str] = set()
        visited: set[tuple[str, ...]] = set()
        reported_total: int | None = None
        last_updated = ""
        pages_checked = raw_rows = rows_skipped = 0
        started = monotonic()
        error = ""
        reached_end = False
        try:
            response = await page.goto(listing.url, wait_until="domcontentloaded", timeout=90000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"HTTP {response.status} from {listing.url}")
            await self._wait_for_rows(page)
            while pages_checked < max_pages:
                if monotonic() - started >= timeout:
                    error = "category timeout reached"
                    break
                rows, total, skipped, page_updated = await self._extract_rows(page, listing)
                if total is not None:
                    reported_total = total
                if page_updated:
                    last_updated = page_updated
                pages_checked += 1
                raw_rows += len(rows) + skipped
                rows_skipped += skipped
                signature = tuple(row.detail_url for row in rows)
                if not signature or signature in visited:
                    error = "pagination returned an empty or duplicate page"
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
                next_url = await self._next_url(page)
                if not next_url or remaining <= 0:
                    reached_end = not next_url
                    break
                await page.goto(next_url, wait_until="domcontentloaded",
                                timeout=min(90000, max(10000, int(remaining * 1000))))
                await self._wait_for_rows(page)
            if reported_total is None and reached_end:
                serials = []
                for record in records:
                    value = dict(record.metadata).get("s_no", "")
                    if value.isdigit():
                        serials.append(int(value))
                if serials:
                    reported_total = max(serials)
            complete = not error and (
                reported_total is None or len(records) >= reported_total
            )
            return ESICCollection(
                records, reported_total, pages_checked, raw_rows,
                rows_skipped, "playwright", complete, last_updated, error,
            )
        except Exception as exc:
            return ESICCollection(
                records, reported_total, pages_checked, raw_rows,
                rows_skipped, "playwright", False, last_updated, str(exc),
            )
        finally:
            await page.close()

    async def _extract_rows(self, page, listing: ESICListing):
        total = await self._reported_total(page)
        last_updated = await self._last_updated(page)
        rows = page.locator("table tbody tr:visible")
        records: list[DocumentRecord] = []
        skipped = 0
        for index in range(await rows.count()):
            cells = rows.nth(index).locator("td")
            if await cells.count() < 6:
                skipped += 1
                continue
            values = [" ".join((await cells.nth(i).inner_text()).split()) for i in range(6)]
            s_no, branch, dated, subject_cell, publish_date, console_sl = values
            links = []
            subject_links = []
            anchors = cells.nth(3).locator("a[href]")
            for link_index in range(await anchors.count()):
                anchor = anchors.nth(link_index)
                href = await anchor.get_attribute("href")
                if not href:
                    continue
                url = urljoin(listing.url, href)
                if not url.lower().startswith(("http://", "https://")):
                    continue
                link_text = " ".join((await anchor.inner_text()).split())
                clean_text = re.sub(r"\s*-\s*PDF\s*size\s*:\s*\([^)]*\)\s*\.?$", "", link_text, flags=re.I).strip()
                if url not in links:
                    links.append(url)
                subject_links.append({"text": clean_text or link_text, "url": url})
            title = " | ".join(item["text"] for item in subject_links if item["text"])
            title = title or subject_cell
            identity = "|".join((branch, dated, console_sl, title))
            stable_url = f"{listing.url}#record-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
            metadata = {
                "s_no": s_no,
                "branch": branch,
                "dated": dated,
                "publish_date": publish_date,
                "console_sl_no": console_sl,
                "last_updated_reviewed": last_updated,
                "subject_links": subject_links,
            }
            records.append(DocumentRecord(
                source="ESIC", category=listing.category,
                record_date=dated, title=title, detail_url=stable_url,
                document_urls=tuple(links),
                metadata=(
                    ("s_no", s_no), ("branch", branch), ("dated", dated),
                    ("publish_date", publish_date), ("console_sl_no", console_sl),
                    ("last_updated_reviewed", last_updated),
                    ("subject_links", json.dumps(subject_links, ensure_ascii=False)),
                ),
            ))
        return records, total, skipped, last_updated

    async def _reported_total(self, page) -> int | None:
        text = " ".join((await page.locator("body").inner_text()).split())
        patterns = (
            r"Showing\s+\d+\s*(?:-|to)\s*\d+\s+of\s+([\d,]+)",
            r"(?:Total|of)\s+([\d,]+)\s+(?:circulars|records|items)\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return int(match.group(1).replace(",", ""))
        # ESIC does not render a total on the first page. During a full
        # reconciliation the final S.No. supplies the authoritative total.
        return None

    async def _last_updated(self, page) -> str:
        text = " ".join((await page.locator("body").inner_text()).split())
        match = re.search(r"Last\s+updated\s*/\s*Reviewed\s*:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text, re.I)
        return match.group(1) if match else ""

    async def _wait_for_rows(self, page) -> None:
        for _ in range(100):
            if await page.locator("table tbody tr:visible").count():
                return
            await page.wait_for_timeout(300)
        raise RuntimeError("ESIC circular rows did not load")

    async def _next_url(self, page) -> str:
        current_match = re.search(r"/page:(\d+)", page.url)
        current = int(current_match.group(1)) if current_match else 1
        links = page.locator('a[href*="/circulars/index/page:"]')
        candidates: list[tuple[int, str, str]] = []
        for index in range(await links.count()):
            link = links.nth(index)
            href = await link.get_attribute("href")
            if not href:
                continue
            match = re.search(r"/page:(\d+)", href)
            if not match:
                continue
            number = int(match.group(1))
            label = " ".join((await link.inner_text()).split()).lower()
            candidates.append((number, label, urljoin(page.url, href)))
        for number, label, href in candidates:
            if number == current + 1 and ("next" in label or label in {"", ">", "»"}):
                return href
        for number, _, href in candidates:
            if number == current + 1:
                return href
        return ""

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
            "last_updated_reviewed": result.last_updated,
            "transport": result.transport, "documents_downloaded": downloaded,
            "document_errors": download_errors,
        }
        if result.error:
            summary["categories"][listing.category]["error"] = result.error

    async def _download(self, record, runtime, download_dir):
        folder = (download_dir or Path("data") / "esic_documents") / "Circular"
        folder.mkdir(parents=True, exist_ok=True)
        downloaded = errors = 0
        for index, url in enumerate(record.document_urls, start=1):
            try:
                response = await runtime.context.request.get(url, timeout=90000)
                if not response.ok:
                    raise RuntimeError(f"PDF HTTP {response.status}")
                data = await response.body()
                if not data.startswith(b"%PDF"):
                    raise RuntimeError("response was not a PDF")
                base = re.sub(r"[^A-Za-z0-9._ -]+", "_", record.title)[:150].strip() or "ESIC_circular"
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
                    event="DOCUMENT_FAILED", occurred_at=utc_now(),
                    document_url=url, message=str(error),
                    metadata_json=json.dumps(dict(record.metadata), ensure_ascii=False),
                ))
                errors += 1
        return downloaded, errors

    def close(self):
        self.state.close()
