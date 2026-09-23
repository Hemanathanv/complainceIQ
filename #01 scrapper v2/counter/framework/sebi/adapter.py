from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from scrapling import Selector

from ..models import AuditEvent, DocumentRecord
from ..runtime import (
    BrowserRuntime,
    fetch_bytes_curl,
    fetch_bytes_httpx,
    fetch_html_curl,
    fetch_html_httpx,
    sha256_bytes,
    utc_now,
)
from ..state import StateStore
from .config import HOME_URL, LISTINGS, SEBIListing


@dataclass
class CollectionResult:
    records: list[DocumentRecord]
    reported_total: int | None
    pages_checked: int
    raw_rows_seen: int
    rows_skipped: int
    transport: str
    complete: bool
    error: str = ""


class SEBIAdapter:
    """SEBI discovery flow with bounded browser and HTTP fallbacks."""

    def __init__(self, state_path: Path, headless: bool = True):
        self.state = StateStore(state_path)
        self.headless = headless

    @staticmethod
    def parse_listing(html: str, listing: SEBIListing) -> tuple[list[DocumentRecord], int | None, int, int]:
        selector = Selector(html)
        records: list[DocumentRecord] = []
        rows = selector.css("table#sample_1 tbody tr")
        skipped_rows = 0
        for row in rows:
            cells = row.css("td")
            if len(cells) < 2:
                skipped_rows += 1
                continue
            links = cells[1].css("a")
            if not links:
                skipped_rows += 1
                continue
            link = links.first
            title = link.text.strip()
            href = link.attrib.get("href", "")
            if not title or not href:
                skipped_rows += 1
                continue
            records.append(DocumentRecord(
                source="SEBI",
                category=listing.category,
                record_date=cells[0].text.strip(),
                title=title,
                detail_url=urljoin("https://www.sebi.gov.in", href),
            ))

        summaries = [node.text.strip() for node in selector.css("#ajax_cat p") if "record" in node.text.lower()]
        total = None
        if summaries:
            match = re.search(r"of\s+(\d+)\s+records", summaries[0], re.I)
            if match:
                total = int(match.group(1))
        return records, total, len(rows), skipped_rows

    async def poll_once(self, max_pages: int = 1, category_timeout: float = 60.0,
                        page_timeout: float = 25.0, download_new: bool = False,
                        download_dir: Path | None = None,
                        download_existing: bool = False) -> dict:
        """Poll both active listings. max_pages=1 is continuous-monitor mode."""
        checked_at = utc_now()
        summary = {"source": "SEBI", "categories": {}, "checked_at": checked_at}
        processed: set[str] = set()
        try:
            async with BrowserRuntime(headless=self.headless) as runtime:
                try:
                    await asyncio.wait_for(runtime.choose_english(HOME_URL), timeout=35.0)
                except Exception:
                    # The language popup is optional after the first visit.
                    pass
                for listing in LISTINGS:
                    result = await self._collect_listing(
                        runtime, listing, max_pages, category_timeout, page_timeout
                    )
                    await self._record_result(
                        summary, listing, result, checked_at,
                        runtime=runtime, download_new=download_new,
                        download_dir=download_dir,
                        download_existing=download_existing,
                    )
                    processed.add(listing.category)
        except Exception as browser_error:
            # Browser startup failures still get a useful HTTP-only poll.
            for listing in LISTINGS:
                if listing.category in processed:
                    continue
                result = await self._collect_http_fallback(
                    listing, max_pages, error_prefix=str(browser_error)
                )
                await self._record_result(
                    summary, listing, result, checked_at,
                    runtime=None, download_new=download_new,
                    download_dir=download_dir,
                    download_existing=download_existing,
                )
        return summary

    async def _collect_listing(self, runtime: BrowserRuntime, listing: SEBIListing,
                               max_pages: int, category_timeout: float,
                               page_timeout: float) -> CollectionResult:
        page = None
        try:
            page = await runtime.new_page()
            result = await asyncio.wait_for(
                self._collect_pages(page, listing, max_pages, page_timeout, category_timeout),
                timeout=category_timeout,
            )
            # Fall back only when the browser produced no usable rows. Partial
            # browser data is retained and reported as partial.
            if not result.records and result.error:
                return await self._collect_http_fallback(listing, max_pages, result.error)
            return result
        except Exception as error:
            return await self._collect_http_fallback(listing, max_pages, str(error))
        finally:
            if page is not None:
                await page.close()

    async def _collect_pages(self, page, listing: SEBIListing, max_pages: int,
                             page_timeout: float, category_timeout: float) -> CollectionResult:
        records: list[DocumentRecord] = []
        seen_urls: set[str] = set()
        visited: set[tuple[str, ...]] = set()
        pages_checked = 0
        total: int | None = None
        raw_rows = 0
        skipped_rows = 0
        started = monotonic()
        error = ""
        try:
            timeout_ms = max(1000, int(page_timeout * 1000))
            await page.goto(listing.url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.locator("table#sample_1").wait_for(state="visible", timeout=timeout_ms)
            while pages_checked < max_pages:
                if monotonic() - started >= category_timeout:
                    error = "category timeout reached"
                    break
                html = await page.content()
                parsed, total, page_rows, page_skipped = self.parse_listing(html, listing)
                pages_checked += 1
                raw_rows += page_rows
                skipped_rows += page_skipped
                signature = tuple(record.detail_url for record in parsed)
                if signature in visited:
                    error = "pagination returned a duplicate page"
                    break
                visited.add(signature)
                for record in parsed:
                    if record.detail_url not in seen_urls:
                        seen_urls.add(record.detail_url)
                        records.append(record)
                if total is not None and len(records) >= total:
                    break
                if pages_checked >= max_pages or not self._has_next_link(html):
                    break
                remaining = category_timeout - (monotonic() - started)
                if remaining <= 0 or not await self._advance_page(
                    page, signature, visited, min(remaining, 20.0)
                ):
                    error = "SEBI pagination did not produce a new page"
                    break
            # A raw row count can reach SEBI's advertised total while the
            # pagination response repeats URLs. Completeness therefore uses
            # unique stable document URLs, not only raw table rows.
            complete = total is None or (
                len(records) >= total and raw_rows >= total and skipped_rows == 0
            )
            return CollectionResult(records, total, pages_checked, raw_rows, skipped_rows,
                                    "playwright+scrapling", complete, error)
        except Exception as exc:
            return CollectionResult(records, total, pages_checked, raw_rows, skipped_rows,
                                    "playwright+scrapling", False, str(exc))

    async def _advance_page(self, page, before: tuple[str, ...], visited: set[tuple[str, ...]],
                            timeout: float) -> bool:
        next_link = page.locator("a", has_text=re.compile(r"^\s*Next", re.I)).last
        if not await next_link.count() or not await next_link.is_visible():
            return False
        timeout_ms = max(1000, int(timeout * 1000))
        for attempt in range(2):
            try:
                async with page.expect_response("**/getnewslistinfo.jsp", timeout=timeout_ms):
                    await next_link.click(timeout=timeout_ms)
                for _ in range(24):
                    await page.wait_for_timeout(250)
                    candidate = await self._table_signature(page)
                    if candidate and candidate != before:
                        await page.wait_for_timeout(500)
                        if await self._table_signature(page) == candidate and candidate not in visited:
                            return True
                        break
            except Exception:
                if attempt == 0:
                    await page.wait_for_timeout(500)
        return False

    async def _collect_http_fallback(self, listing: SEBIListing, max_pages: int,
                                     error_prefix: str = "") -> CollectionResult:
        errors: list[str] = []
        for name, fetcher in (("httpx", fetch_html_httpx), ("curl_cffi", fetch_html_curl)):
            try:
                html = await asyncio.wait_for(fetcher(listing.url), timeout=18.0)
                records, total, raw_rows, skipped = self.parse_listing(html, listing)
                if not records:
                    raise RuntimeError("listing table contained no usable records")
                # HTTP fallback safely handles only the first page.
                return CollectionResult(records, total, 1, raw_rows, skipped,
                                        f"{name}+scrapling", total is None or raw_rows >= total,
                                        "; ".join(errors))
            except Exception as error:
                errors.append(f"{name}: {error}")
        prefix = f"{error_prefix}; " if error_prefix else ""
        return CollectionResult([], None, 0, 0, 0, "unavailable", False,
                                prefix + "; ".join(errors))

    async def _record_result(self, summary: dict, listing: SEBIListing,
                             result: CollectionResult, checked_at: str, *,
                             runtime: BrowserRuntime | None, download_new: bool,
                             download_dir: Path | None,
                             download_existing: bool) -> None:
        new_count = 0
        downloaded_count = 0
        download_errors = 0
        for record in result.records:
            is_new = self.state.upsert(record, checked_at)
            self.state.audit(AuditEvent(
                fingerprint=record.fingerprint, source=record.source,
                category=record.category, record_date=record.record_date,
                title=record.title, detail_url=record.detail_url,
                event="NEW_RECORD" if is_new else "SEEN_RECORD",
                occurred_at=checked_at, transport=result.transport,
            ))
            new_count += int(is_new)
            if (is_new or download_existing) and download_new:
                downloaded, errors = await self._download_documents(
                    record, runtime, download_dir
                )
                downloaded_count += downloaded
                download_errors += errors

        coverage_warning = ""
        if result.raw_rows_seen > len(result.records):
            coverage_warning = "duplicate document URLs returned during pagination"
        elif result.pages_checked > 1 and result.reported_total is not None and not result.complete:
            coverage_warning = "unique records did not reach the reported total"

        status = "warning" if coverage_warning else "ok"
        if result.error:
            status = "partial" if result.records else "failed"
            self.state.audit(AuditEvent(
                fingerprint=f"SEBI|{listing.category}|{checked_at}",
                source="SEBI", category=listing.category, record_date="", title="",
                detail_url=listing.url,
                event="CHECK_PARTIAL" if result.records else "CHECK_FAILED",
                occurred_at=checked_at, transport=result.transport, message=result.error,
            ))
        category = {
            "status": status,
            "records_checked": len(result.records),
            "new_records": new_count,
            "reported_total": result.reported_total,
            "pages_checked": result.pages_checked,
            "raw_rows_seen": result.raw_rows_seen,
            "rows_skipped": result.rows_skipped,
            "duplicate_rows": result.raw_rows_seen - len(result.records),
            "complete": result.complete,
            "transport": result.transport,
            "documents_downloaded": downloaded_count,
            "document_errors": download_errors,
        }
        if coverage_warning:
            category["coverage_warning"] = coverage_warning
        if result.error:
            category["error"] = result.error
        summary["categories"][listing.category] = category

    async def _download_documents(self, record: DocumentRecord,
                                  runtime: BrowserRuntime | None,
                                  download_dir: Path | None) -> tuple[int, int]:
        """Discover and download PDFs for one newly observed listing record."""
        destination = (download_dir or Path("data") / "sebi_documents") / record.category
        urls: list[str] = []
        errors: list[str] = []
        if self._looks_like_pdf(record.detail_url):
            urls.append(record.detail_url)
        else:
            if runtime is not None:
                try:
                    urls = await self._discover_document_urls_browser(runtime, record.detail_url)
                except Exception as error:
                    errors.append(f"browser discovery: {error}")
            if not urls:
                try:
                    urls = await self._discover_document_urls_http(record.detail_url)
                except Exception as error:
                    errors.append(f"HTTP discovery: {error}")

        if not urls:
            self._audit_document_event(
                record, "DOCUMENT_NOT_FOUND",
                message="; ".join(errors) or "No PDF link found",
            )
            return 0, 1

        downloaded = 0
        failures = 0
        for index, document_url in enumerate(dict.fromkeys(urls), 1):
            try:
                data, transport = await self._download_bytes(runtime, document_url)
                if not data.startswith(b"%PDF"):
                    raise RuntimeError("response was not a PDF")
                destination.mkdir(parents=True, exist_ok=True)
                suffix = "" if index == 1 else f"_{index:02d}"
                path = destination / self._document_filename(record, suffix)
                path.write_bytes(data)
                self._audit_document_event(
                    record, "DOCUMENT_DOWNLOADED", document_url=document_url,
                    file_path=str(path.resolve()), file_sha256=sha256_bytes(data),
                    transport=transport,
                )
                downloaded += 1
            except Exception as error:
                failures += 1
                self._audit_document_event(
                    record, "DOCUMENT_FAILED", document_url=document_url,
                    message=str(error),
                )
        return downloaded, failures

    async def _discover_document_urls_browser(self, runtime: BrowserRuntime,
                                              detail_url: str) -> list[str]:
        page = await runtime.new_page()
        try:
            await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(500)
            values: list[str] = []
            for locator in (page.locator("a[href]"), page.locator("iframe[src]")):
                for element in await locator.all():
                    value = await element.get_attribute("href") or await element.get_attribute("src")
                    if value:
                        candidate = self._normalize_document_url(value)
                        if candidate:
                            values.append(candidate)
            return list(dict.fromkeys(values))
        finally:
            await page.close()

    async def _discover_document_urls_http(self, detail_url: str) -> list[str]:
        html = await asyncio.wait_for(fetch_html_httpx(detail_url), timeout=18.0)
        selector = Selector(html)
        values: list[str] = []
        for node in selector.css("a") + selector.css("iframe"):
            value = node.attrib.get("href") or node.attrib.get("src")
            if value:
                candidate = self._normalize_document_url(value)
                if candidate:
                    values.append(candidate)
        return list(dict.fromkeys(values))

    async def _download_bytes(self, runtime: BrowserRuntime | None,
                              url: str) -> tuple[bytes, str]:
        if runtime is not None and runtime.context is not None:
            response = await runtime.context.request.get(url, timeout=30000)
            if not response.ok:
                raise RuntimeError(f"HTTP {response.status} while downloading PDF")
            return await response.body(), "playwright-request"
        errors: list[str] = []
        for name, fetcher in (("httpx", fetch_bytes_httpx), ("curl_cffi", fetch_bytes_curl)):
            try:
                return await asyncio.wait_for(fetcher(url), timeout=35.0), name
            except Exception as error:
                errors.append(f"{name}: {error}")
        raise RuntimeError("; ".join(errors))

    def _audit_document_event(self, record: DocumentRecord, event: str, *,
                              document_url: str = "", file_path: str = "",
                              file_sha256: str = "", transport: str = "",
                              message: str = "") -> None:
        self.state.audit(AuditEvent(
            fingerprint=record.fingerprint, source=record.source,
            category=record.category, record_date=record.record_date,
            title=record.title, detail_url=record.detail_url,
            event=event, occurred_at=utc_now(), document_url=document_url,
            file_path=file_path, file_sha256=file_sha256,
            transport=transport, message=message,
        ))

    @staticmethod
    def _normalize_document_url(value: str) -> str:
        if "file=" in value:
            value = unquote(parse_qs(urlparse(value).query).get("file", [""])[0])
        candidate = urljoin("https://www.sebi.gov.in", value)
        lowered = candidate.lower()
        if ".pdf" in lowered or "/sebi_data/attachdocs/" in lowered:
            return candidate
        return ""

    @staticmethod
    def _looks_like_pdf(url: str) -> bool:
        lowered = url.lower()
        return ".pdf" in lowered or "/sebi_data/attachdocs/" in lowered

    @staticmethod
    def _document_filename(record: DocumentRecord, suffix: str) -> str:
        title = re.sub(r"\s+", " ", record.title).strip()
        title = re.sub(r'[<>:"/\\|?*]', "_", title)[:150] or "SEBI_document"
        fingerprint = hashlib.sha256(record.fingerprint.encode("utf-8")).hexdigest()[:10]
        date = re.sub(r"[^0-9A-Za-z-]+", "_", record.record_date) or "undated"
        return f"{date}_{title}_{fingerprint}{suffix}.pdf"

    @staticmethod
    def _has_next_link(html: str) -> bool:
        selector = Selector(html)
        links = selector.css("#ajax_cat a") or selector.css("a")
        return any(re.search(r"^\s*Next", link.text, re.I) for link in links)

    @staticmethod
    async def _table_signature(page) -> tuple[str, ...]:
        rows = await page.locator("table#sample_1 tbody tr").all()
        values = []
        for row in rows:
            links = await row.locator("td:nth-child(2) a").all()
            if links:
                values.append(await links[0].get_attribute("href") or "")
        return tuple(values)

    def close(self) -> None:
        self.state.close()
