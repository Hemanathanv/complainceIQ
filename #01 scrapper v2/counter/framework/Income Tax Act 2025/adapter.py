from __future__ import annotations

import asyncio
import hashlib
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
from .config import IncomeTaxListing, LISTINGS


class StandaloneCamoufoxRuntime:
    """Standalone browser runtime; does not depend on an existing Chrome session."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._camoufox = None
        self.browser = None
        self.context = None

    async def __aenter__(self):
        self._camoufox = AsyncCamoufox(
            headless=self.headless, humanize=True, os="windows"
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
class IncomeTaxCollection:
    records: list[DocumentRecord]
    reported_total: int | None
    pages_checked: int
    raw_rows_seen: int
    transport: str
    complete: bool
    error: str = ""


class IncomeTaxAdapter:
    """Dynamic discovery for the Income Tax Department's two target lists."""

    def __init__(self, state_path: Path, headless: bool = True):
        self.state = StateStore(state_path)
        self.headless = headless

    async def poll_once(self, *, max_pages: int = 1,
                        category_timeout: float = 180.0,
                        download_new: bool = False,
                        download_dir: Path | None = None,
                        download_existing: bool = False) -> dict:
        checked_at = utc_now()
        summary = {"source": "Income Tax Department", "categories": {},
                   "checked_at": checked_at}
        try:
            async with StandaloneCamoufoxRuntime(headless=self.headless) as runtime:
                for listing in LISTINGS:
                    page_totals = {"new": 0, "downloaded": 0, "errors": 0}

                    async def process_page(rows, total, page_number, raw_rows, page):
                        new_count, downloaded, errors = await self._record_page(
                            listing, rows, checked_at, runtime,
                            download_new, download_dir, download_existing, page,
                        )
                        page_totals["new"] += new_count
                        page_totals["downloaded"] += downloaded
                        page_totals["errors"] += errors

                    result = await self._collect_listing(
                        runtime, listing, max_pages=max_pages,
                        timeout=category_timeout,
                        on_page=process_page,
                    )
                    status = "ok" if not result.error else ("partial" if result.records else "failed")
                    summary["categories"][listing.category] = {
                        "status": status, "records_checked": len(result.records),
                        "new_records": page_totals["new"],
                        "reported_total": result.reported_total,
                        "pages_checked": result.pages_checked,
                        "raw_rows_seen": result.raw_rows_seen,
                        "complete": result.complete and not result.error,
                        "transport": result.transport,
                        "documents_downloaded": page_totals["downloaded"],
                        "document_errors": page_totals["errors"],
                    }
                    if result.error:
                        summary["categories"][listing.category]["error"] = result.error
        except Exception as error:
            for listing in LISTINGS:
                if listing.category not in summary["categories"]:
                    summary["categories"][listing.category] = {
                        "status": "failed", "records_checked": 0,
                        "new_records": 0, "reported_total": None,
                        "pages_checked": 0, "raw_rows_seen": 0,
                        "complete": False, "transport": "unavailable",
                        "error": str(error),
                    }
        return summary

    async def _collect_listing(self, runtime: BrowserRuntime,
                               listing: IncomeTaxListing, *, max_pages: int,
                               timeout: float, on_page=None) -> IncomeTaxCollection:
        page = await runtime.new_page()
        records: list[DocumentRecord] = []
        seen: set[str] = set()
        visited: set[tuple[str, ...]] = set()
        pages_checked = 0
        raw_rows_seen = 0
        reported_total: int | None = None
        started = monotonic()
        error = ""
        try:
            response = await page.goto(
                listing.url, wait_until="domcontentloaded", timeout=90000
            )
            if response is not None and response.status >= 400:
                raise RuntimeError(f"HTTP {response.status} from {listing.url}")
            await self._wait_for_cards(page)
            api_result = await self._collect_api_listing(
                page, listing, max_pages=max_pages, timeout=timeout,
                on_page=on_page,
            )
            if api_result is not None:
                return api_result
            while pages_checked < max_pages:
                if monotonic() - started >= timeout:
                    error = "category timeout reached"
                    break
                rows, total = await self._extract_cards(page, listing)
                if total is not None:
                    reported_total = total
                pages_checked += 1
                raw_rows_seen += len(rows)
                signature = tuple(
                    f"{row.title}|{row.record_date}|{row.detail_url}" for row in rows
                )
                if signature in visited:
                    error = "pagination returned a duplicate page"
                    break
                visited.add(signature)
                if on_page is not None:
                    await on_page(rows, reported_total, pages_checked, raw_rows_seen, page)
                for row in rows:
                    if row.detail_url not in seen:
                        seen.add(row.detail_url)
                        records.append(row)
                if reported_total is not None and len(records) >= reported_total:
                    break
                if pages_checked >= max_pages:
                    break
                if not await self._advance_page(
                    page, listing, signature, timeout - (monotonic() - started)
                ):
                    break
            complete = not error and (
                reported_total is None or len(records) >= reported_total
            )
            return IncomeTaxCollection(
                records, reported_total, pages_checked, raw_rows_seen,
                "playwright", complete, error,
            )
        except Exception as exc:
            return IncomeTaxCollection(
                records, reported_total, pages_checked, raw_rows_seen,
                "playwright", False, str(exc),
            )
        finally:
            await page.close()

    async def _collect_api_listing(self, page, listing: IncomeTaxListing, *,
                                   max_pages: int, timeout: float, on_page=None):
        """Use the listing's own batched search endpoint when available.

        The public listing renders ten cards at a time, but its frontend fetches
        the complete result set from this endpoint.  Using that same endpoint
        avoids reopening the listing page for every document.
        """
        visitor_resource = await page.evaluate(
            """() => performance.getEntriesByType('resource').map(e => e.name)
                .find(u => u.includes('/etds/visitors/')) || ''"""
        )
        visitor_id = str(visitor_resource or '').rstrip('/').rsplit('/', 1)[-1]
        if not visitor_id or visitor_id == str(visitor_resource):
            return None
        structure_key = (
            "NOTIFICATION_KEY" if listing.category == "Notification"
            else "CIRCULAR_KEY"
        )
        api_base = "https://www.incometaxindia.gov.in/o/search/v1.0/search"
        body = {
            "attributes": {
                "search.empty.search": True,
                "search.experiences.blueprint.external.reference.code":
                    "CIRCULAR_NOTIFICATION_BP_ERC",
                "search.experiences.structure_id": "36050",
                "search.experiences.structure_key": structure_key,
            }
        }
        records: list[DocumentRecord] = []
        seen: set[str] = set()
        reported_total = None
        pages_checked = 0
        raw_rows_seen = 0
        started = monotonic()
        for api_page in range(1, max_pages + 1):
            if monotonic() - started >= timeout:
                return IncomeTaxCollection(
                    records, reported_total, pages_checked, raw_rows_seen,
                    "income-tax-search-api", False, "category timeout reached",
                )
            url = f"{api_base}?nestedFields=embedded&page={api_page}&pageSize=500&restrictFields=embedded.actions%2Cembedded.creator"
            result = await page.evaluate(
                """async ({url, body, visitorId}) => {
                    const response = await fetch(url, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Visitor-ID': visitorId
                        },
                        body: JSON.stringify(body)
                    });
                    const text = await response.text();
                    let json = null;
                    try { json = JSON.parse(text); } catch (_) {}
                    return {status: response.status, json, text: text.slice(0, 500)};
                }""",
                {"url": url, "body": body, "visitorId": visitor_id},
            )
            if result.get("status", 0) >= 400 or not result.get("json"):
                raise RuntimeError(
                    f"Income Tax search API HTTP {result.get('status')}: "
                    f"{result.get('text', '')}"
                )
            payload = result["json"]
            items = payload.get("items") or []
            reported_total = payload.get("totalCount")
            last_page = int(payload.get("lastPage") or 0)
            rows = self._records_from_api(items, listing)
            pages_checked += 1
            raw_rows_seen += len(rows)
            if on_page is not None:
                await on_page(rows, reported_total, pages_checked, raw_rows_seen, page)
            for row in rows:
                if row.detail_url not in seen:
                    seen.add(row.detail_url)
                    records.append(row)
            if not items or (reported_total is not None and len(records) >= reported_total):
                break
            if last_page and api_page >= last_page:
                break
        complete = bool(reported_total is not None and len(records) >= reported_total)
        return IncomeTaxCollection(
            records, reported_total, pages_checked, raw_rows_seen,
            "income-tax-search-api", complete, "" if complete else "incomplete API result",
        )

    def _records_from_api(self, items, listing: IncomeTaxListing):
        records = []
        for item in items:
            fields = {}
            for field in (item.get("embedded", {}).get("contentFields") or []):
                name = field.get("name")
                if name:
                    fields[name] = field.get("contentFieldValue") or {}
            title_field = fields.get("circularNotificationNumber", {})
            title_value = (
                title_field.get("data") or title_field.get("value")
                or item.get("title") or item.get("name")
            )
            title = str(title_value or "").strip()
            date_value = (
                fields.get("circularNotificationDate", {}).get("data")
                or fields.get("uploadDate", {}).get("data")
                or ""
            )
            record_date = str(date_value).strip()
            if "T" in record_date:
                record_date = record_date.split("T", 1)[0]
            report = fields.get("reportFile", {}).get("document") or {}
            content_url = str(report.get("contentUrl") or "").strip()
            direct_url = urljoin("https://www.incometaxindia.gov.in", content_url) if content_url else ""
            api_id = str(item.get("id") or item.get("key") or "").strip()
            if not title:
                title = (
                    f"{listing.category} record {api_id}"
                    if api_id else f"{listing.category} record {len(records) + 1}"
                )
            stable_key = hashlib.sha256(
                f"{listing.category}|{title}|{record_date}|{direct_url}|{api_id}".encode()
            ).hexdigest()[:24]
            detail_url = f"{listing.url}#api-{stable_key}"
            metadata = [("api_id", api_id)] if api_id else []
            if content_url:
                metadata.append(("content_url", content_url))
            records.append(DocumentRecord(
                source="Income Tax Department", category=listing.category,
                record_date=record_date, title=title, detail_url=detail_url,
                document_urls=(direct_url,) if direct_url else tuple(),
                metadata=tuple(metadata),
            ))
        return records

    async def _record_page(self, listing, records, checked_at, runtime,
                           download_new, download_dir, download_existing,
                           listing_page):
        new_count = downloaded = errors = 0
        direct_downloads = []
        for record in records:
            is_new = self.state.upsert(record, checked_at)
            self.state.audit(AuditEvent(
                fingerprint=record.fingerprint, source=record.source,
                category=record.category, record_date=record.record_date,
                title=record.title, detail_url=record.detail_url,
                event="NEW_RECORD" if is_new else "SEEN_RECORD",
                occurred_at=checked_at, transport="playwright",
                metadata_json=json.dumps(dict(record.metadata), ensure_ascii=False),
            ))
            new_count += int(is_new)
            if (is_new or download_existing) and download_new:
                if record.document_urls:
                    direct_downloads.append(record)
                else:
                    count, failed = await self._download_record(
                        record, runtime, download_dir, listing_page=listing_page
                    )
                    downloaded += count
                    errors += failed
        if direct_downloads:
            semaphore = asyncio.Semaphore(24)

            async def download_direct(record):
                async with semaphore:
                    return await self._download_record(record, runtime, download_dir)

            results = await asyncio.gather(*(
                download_direct(record)
                for record in direct_downloads
            ))
            downloaded += sum(result[0] for result in results)
            errors += sum(result[1] for result in results)
        return new_count, downloaded, errors

    async def _extract_cards(self, page, listing: IncomeTaxListing):
        cards = page.locator("#listViewContent .card")
        if not await cards.count():
            cards = page.locator(".card")
        total = await self._reported_total(page)
        records: list[DocumentRecord] = []
        prefix = "Notification No" if listing.category == "Notification" else "Circular No"
        for index in range(await cards.count()):
            card = cards.nth(index)
            title_locator = card.locator("button.card-title")
            if not await title_locator.count():
                title_locator = card.locator("a")
            title = ""
            href = ""
            for candidate in await title_locator.all():
                candidate_text = (await candidate.inner_text()).strip()
                if candidate_text.startswith(prefix):
                    title = candidate_text
                    href = await candidate.get_attribute("href") or ""
                    break
            if not title:
                continue
            text = await card.inner_text()
            date_match = re.search(r"Published\s+On\s*:\s*([^\n]+)", text, re.I)
            record_date = date_match.group(1).strip() if date_match else ""
            tags = []
            for tag in await card.locator(".tags span, .new-badge span").all():
                value = " ".join((await tag.inner_text()).split()).strip()
                if value and value not in tags:
                    tags.append(value)
            stable_key = hashlib.sha256(
                f"Income Tax Department|{listing.category}|{record_date}|{title}".encode()
            ).hexdigest()[:24]
            detail_url = urljoin(listing.url, href) if href else f"{listing.url}#record-{stable_key}"
            records.append(DocumentRecord(
                source="Income Tax Department", category=listing.category,
                record_date=record_date, title=title, detail_url=detail_url,
                metadata=(("tags", "|".join(tags)),) if tags else tuple(),
            ))
        return records, total

    async def _reported_total(self, page) -> int | None:
        text = " ".join((await page.locator("body").inner_text()).split())
        match = re.search(
            r"(?:\bof|etds-pagination-of)\s+([\d,]+)\s+(?:items|etds-items)\b",
            text, re.I,
        )
        return int(match.group(1).replace(",", "")) if match else None

    async def _wait_for_cards(self, page) -> None:
        for _ in range(40):
            if await page.locator("#listViewContent .card").count() or await page.locator(".card").count():
                return
            await page.wait_for_timeout(500)
        raise RuntimeError("document cards did not load")

    async def _advance_page(self, page, listing: IncomeTaxListing,
                            before: tuple[str, ...], timeout: float) -> bool:
        if timeout <= 0:
            return False
        next_button = None
        for selector in (
            "#pagination-next-button", "button[aria-label*='next' i]",
            "a[aria-label*='next' i]", "button:has-text('Next')",
            "a:has-text('Next')", "button:has-text('›')", "a:has-text('›')",
        ):
            for candidate in await page.locator(selector).all():
                if await candidate.is_visible() and not await candidate.is_disabled():
                    next_button = candidate
                    break
            if next_button:
                break
        if next_button is None:
            return False
        await next_button.click(timeout=min(20000, int(timeout * 1000)), force=True)
        for _ in range(60):
            await page.wait_for_timeout(300)
            rows, _ = await self._extract_cards(page, listing)
            current = tuple(f"{row.title}|{row.record_date}|{row.detail_url}" for row in rows)
            if current and current != before:
                return True
        return False

    async def _record_result(self, summary, listing, result, checked_at,
                             runtime, download_new, download_dir,
                             download_existing):
        new_count = 0
        downloaded = 0
        errors = 0
        for record in result.records:
            is_new = self.state.upsert(record, checked_at)
            self.state.audit(AuditEvent(
                fingerprint=record.fingerprint, source=record.source,
                category=record.category, record_date=record.record_date,
                title=record.title, detail_url=record.detail_url,
                event="NEW_RECORD" if is_new else "SEEN_RECORD",
                occurred_at=checked_at, transport=result.transport,
                metadata_json=json.dumps(dict(record.metadata), ensure_ascii=False),
            ))
            new_count += int(is_new)
            if (is_new or download_existing) and download_new:
                count, failed = await self._download_record(record, runtime, download_dir)
                downloaded += count
                errors += failed
        status = "ok" if not result.error else ("partial" if result.records else "failed")
        summary["categories"][listing.category] = {
            "status": status, "records_checked": len(result.records),
            "new_records": new_count, "reported_total": result.reported_total,
            "pages_checked": result.pages_checked, "raw_rows_seen": result.raw_rows_seen,
            "complete": result.complete and not result.error,
            "transport": result.transport, "documents_downloaded": downloaded,
            "document_errors": errors,
        }
        if result.error:
            summary["categories"][listing.category]["error"] = result.error

    async def _download_record(self, record, runtime, download_dir, listing_page=None):
        page = None
        owns_page = False
        popup = None
        try:
            if record.document_urls:
                direct_url = record.document_urls[0]
                response = await runtime.context.request.get(direct_url, timeout=90000)
                data = await response.body() if response.ok else b""
                if response.ok and data.startswith(b"%PDF"):
                    return await self._save_pdf(record, data, direct_url, download_dir)
            page = listing_page or await runtime.new_page()
            owns_page = listing_page is None
            if "#record-" in record.detail_url:
                source_url = record.detail_url.split("#", 1)[0]
                if owns_page:
                    response = await page.goto(source_url, wait_until="domcontentloaded", timeout=90000)
                    if response is not None and response.status >= 400:
                        raise RuntimeError(f"HTTP {response.status} from listing page")
                    await self._wait_for_cards(page)
                title_button = page.locator("#listViewContent .card").filter(has_text=record.title).locator("button.card-title").first
                if not await title_button.count():
                    title_button = page.locator("button.card-title").filter(has_text=record.title).first
                if not await title_button.count():
                    raise RuntimeError("record button not found on listing page")
                try:
                    async with runtime.context.expect_page(timeout=15000) as popup_info:
                        await title_button.click(timeout=15000, force=True)
                    popup = await popup_info.value
                    await popup.wait_for_load_state("domcontentloaded", timeout=90000)
                    await popup.wait_for_timeout(800)
                    pdf_url = popup.url
                    source_page = popup
                except Exception:
                    await title_button.click(timeout=15000, force=True)
                    await page.wait_for_timeout(1000)
                    pdf_url = page.url if "/documents/d/" in page.url else await self._find_pdf_url(page)
                    source_page = page
            else:
                response = await page.goto(record.detail_url, wait_until="domcontentloaded", timeout=90000)
                if response is not None and response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status} from detail page")
                await page.wait_for_timeout(800)
                pdf_url = await self._find_pdf_url(page)
                source_page = page
            if not pdf_url:
                button = source_page.get_by_text(re.compile(r"^Download PDF$", re.I)).last
                if await button.count() and await button.is_visible():
                    async with source_page.expect_download(timeout=15000) as info:
                        await button.click()
                    data = await (await info.value.create_read_stream()).read()
                else:
                    raise RuntimeError("PDF link not found")
            else:
                response = await runtime.context.request.get(pdf_url, timeout=90000)
                if not response.ok:
                    raise RuntimeError(f"PDF HTTP {response.status}")
                data = await response.body()
            if not data.startswith(b"%PDF"):
                raise RuntimeError("download was not a PDF")
            return await self._save_pdf(
                record, data, pdf_url if 'pdf_url' in locals() else record.detail_url,
                download_dir,
            )
        except Exception as error:
            self.state.audit(AuditEvent(
                fingerprint=record.fingerprint, source=record.source,
                category=record.category, record_date=record.record_date,
                title=record.title, detail_url=record.detail_url,
                event="DOCUMENT_FAILED", occurred_at=utc_now(), message=str(error),
                metadata_json=json.dumps(dict(record.metadata), ensure_ascii=False),
            ))
            return 0, 1
        finally:
            if popup is not None:
                await popup.close()
            if owns_page:
                await page.close()

    async def _save_pdf(self, record, data, document_url, download_dir):
        folder = (download_dir or Path("data") / "income_tax_documents") / record.category
        folder.mkdir(parents=True, exist_ok=True)
        filename = re.sub(r"[^A-Za-z0-9._ -]+", "_", record.title)[:160].strip() or "document"
        path = folder / f"{filename}_{record.fingerprint[:10]}.pdf"
        path.write_bytes(data)
        self.state.audit(AuditEvent(
            fingerprint=record.fingerprint, source=record.source,
            category=record.category, record_date=record.record_date,
            title=record.title, detail_url=record.detail_url,
            event="DOCUMENT_DOWNLOADED", occurred_at=utc_now(),
            document_url=document_url, file_path=str(path.resolve()),
            file_sha256=sha256_bytes(data), transport="income-tax-direct-request",
            metadata_json=json.dumps(dict(record.metadata), ensure_ascii=False),
        ))
        return 1, 0

    async def _find_pdf_url(self, page):
        for selector in (
            "a[href*='.pdf' i]", "iframe[src*='.pdf' i]",
            "embed[src*='.pdf' i]", "object[data*='.pdf' i]",
        ):
            for element in await page.locator(selector).all():
                ref = await element.get_attribute("href") or await element.get_attribute("src") or await element.get_attribute("data")
                if ref:
                    return urljoin(page.url, ref)
        return None

    def close(self):
        self.state.close()
