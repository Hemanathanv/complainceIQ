from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from ..models import AuditEvent, DocumentRecord
from ..runtime import (
    BrowserRuntime,
    fetch_bytes_curl,
    fetch_bytes_httpx,
    sha256_bytes,
    utc_now,
)
from ..state import StateStore
from .config import BASE_URL, GST_TAX_ID, SECTIONS, GSTSection


@dataclass
class GSTCollection:
    records: list[DocumentRecord]
    pages_checked: int
    raw_rows_seen: int
    transport: str
    complete: bool
    error: str = ""


class GSTAdapter:
    """CBIC GST Circular and Notification monitor."""

    def __init__(self, state_path: Path, headless: bool = True, from_year: int = 2017):
        self.state = StateStore(state_path)
        self.headless = headless
        self.from_year = from_year
        self._api_token = ""

    async def poll_once(self, *, baseline: bool = False, max_pages: int = 1,
                        category_timeout: float = 120.0,
                        download_new: bool = False,
                        download_dir: Path | None = None,
                        download_existing: bool = False) -> dict:
        checked_at = utc_now()
        summary = {"source": "CBIC GST", "categories": {}, "checked_at": checked_at}
        runtime: BrowserRuntime | None = None
        try:
            async with BrowserRuntime(
                headless=self.headless, ignore_https_errors=True
            ) as active_runtime:
                runtime = active_runtime
                page = await runtime.new_page()
                try:
                    await asyncio.wait_for(
                        self._open_gst_home(page), timeout=40.0
                    )
                    for section in SECTIONS:
                        started = monotonic()
                        if section.name == "Circular":
                            result = await asyncio.wait_for(
                                self._discover_circulars(page, baseline),
                                timeout=category_timeout,
                            )
                        else:
                            result = await asyncio.wait_for(
                                self._discover_notifications(
                                    page, section, baseline, max_pages,
                                    category_timeout - (monotonic() - started),
                                ),
                                timeout=max(5.0, category_timeout - (monotonic() - started)),
                            )
                        await self._record_result(
                            summary, section, result, checked_at,
                            runtime=runtime, download_new=download_new,
                            download_dir=download_dir,
                            download_existing=download_existing,
                        )
                finally:
                    await page.close()
        except Exception as browser_error:
            for section in SECTIONS:
                if section.name not in summary["categories"]:
                    result = GSTCollection([], 0, 0, "unavailable", False, str(browser_error))
                    await self._record_result(
                        summary, section, result, checked_at,
                        runtime=None, download_new=False, download_dir=download_dir,
                        download_existing=False,
                    )
        return summary

    async def count_filters(self, *, timeout: float = 900.0) -> dict:
        """Read the portal's own total for every category/year filter.

        This deliberately does not count downloaded files and does not open PDF
        links.  The portal's result summary is the authority for this report.
        """
        checked_at = utc_now()
        report: dict = {
            "source": "CBIC GST",
            "checked_at": checked_at,
            "basis": "portal filter total endpoint",
            "sections": {},
            "errors": [],
        }
        deadline = monotonic() + timeout
        async with BrowserRuntime(
            headless=self.headless, ignore_https_errors=True
        ) as runtime:
            page = await runtime.new_page()
            try:
                try:
                    await self._open_gst_home(page)
                except Exception as error:
                    report["errors"].append(
                        f"portal: {type(error).__name__}: {error}"
                    )
                    return report | {"complete": False}
                for section in SECTIONS:
                    if monotonic() >= deadline:
                        raise TimeoutError("GST count report timeout reached")
                    try:
                        values = await self._count_section_filters(
                            page, section, deadline
                        )
                        report["sections"][section.name] = values
                    except Exception as error:
                        report["errors"].append(
                            f"{section.name}: {type(error).__name__}: {error}"
                        )
            finally:
                await page.close()
        report["complete"] = not report["errors"] and len(report["sections"]) == len(SECTIONS)
        return report

    async def _count_section_filters(self, page, section: GSTSection,
                                     deadline: float) -> dict:
        await self._click_tab(page, section)
        pane = page.locator("body")
        category_select = await self._visible_locator(pane, section.category_id)
        year_select = await self._visible_locator(pane, section.year_id)
        if year_select is None:
            raise RuntimeError("year selector not found")

        categories = await self._options(category_select) if category_select else [("", "All")]
        years = [
            pair for pair in await self._options(year_select)
            if pair[1].isdigit() and int(pair[1]) >= self.from_year
        ]
        years.sort(key=lambda pair: int(pair[1]), reverse=True)
        if not categories:
            categories = [("", "All")]
        if not years:
            raise RuntimeError("no year options found")

        result: dict[str, dict[str, int]] = {}
        for category_value, category_label in categories:
            if monotonic() >= deadline:
                raise TimeoutError("count report timeout reached")
            result[category_label] = {}
            for year_value, year_label in years:
                if monotonic() >= deadline:
                    raise TimeoutError("count report timeout reached")
                endpoint = (
                    f"/api/{section.endpoint}"
                )
                pane = page.locator("body")
                category_select = await self._visible_locator(pane, section.category_id)
                year_select = await self._visible_locator(pane, section.year_id)
                if category_select and category_value:
                    await category_select.select_option(category_value)
                    await page.wait_for_timeout(700)
                if year_select is None:
                    raise RuntimeError("year selector not found")

                # Force a real change when the requested year is already
                # selected; otherwise some portal versions emit no request.
                current_year = await year_select.input_value()
                if current_year == year_value and len(years) > 1:
                    temporary = next(value for value, _ in years if value != year_value)
                    await year_select.select_option(temporary)
                    await page.wait_for_timeout(700)

                async with page.expect_response(
                    lambda response: self._is_filter_total_response(
                        response, endpoint, year_value, category_value
                    ),
                    timeout=60000,
                ) as response_info:
                    await year_select.select_option(year_value)
                response = await response_info.value
                if response.status >= 400:
                    raise RuntimeError(f"GST count HTTP {response.status}: {response.url}")
                payload = await response.json()
                result[category_label][year_label] = self._payload_total(payload)
        return result

    @staticmethod
    def _is_filter_total_response(response, endpoint: str, year: str,
                                  category: str) -> bool:
        parsed = urlparse(response.url)
        query = parse_qs(parsed.query)
        return (
            parsed.path == endpoint
            and query.get("year") == [year]
            and query.get("category") == [category]
        )

    @staticmethod
    def _payload_total(payload) -> int:
        if isinstance(payload, int):
            return payload
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict):
            for key in ("totalElements", "total", "count", "totalCount"):
                value = payload.get(key)
                if isinstance(value, int):
                    return value
            for key in ("content", "data", "records", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return len(value)
        raise RuntimeError("GST count endpoint returned an unexpected payload")

    async def _wait_for_selected_result(self, pane, selected_year: str,
                                        previous_signature: tuple[str, ...]) -> None:
        """Wait until the portal table matches the selected year.

        A simple row-count wait can accept the previous filter's table because
        the CBIC page refreshes asynchronously. Require the visible row years
        to match the requested year, while allowing a genuine zero-result
        summary.
        """
        deadline = monotonic() + 45.0
        matched = 0
        while monotonic() < deadline:
            rows = pane.locator("table.table-hover:visible tbody tr")
            signature = await self._row_signature(rows)
            summary = await self._summary_text(pane)
            total = self._read_total_from_text(summary)
            no_result = await self._has_no_result_message(pane)
            years: set[str] = set()
            for index in range(await rows.count()):
                cells = rows.nth(index).locator("td")
                if await cells.count() >= 2:
                    years.update(re.findall(r"\b(?:19|20)\d{2}\b",
                                            await cells.nth(1).inner_text()))
            # Some zero-result filters retain the previous table rows in the
            # DOM while the portal summary already says "of 0 items". The
            # summary is the authoritative signal for a genuine zero.
            zero_result = total == 0 or no_result
            year_matches = bool(years) and years == {selected_year}
            if zero_result or year_matches:
                matched += 1
                if matched >= 2:
                    return
            else:
                matched = 0
            await pane.page.wait_for_timeout(500)
        raise RuntimeError(
            f"GST filter did not settle for selected year {selected_year}"
        )

    async def _open_gst_home(self, page) -> None:
        response = await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=90000)
        if response is not None and response.status >= 400:
            raise RuntimeError(f"CBIC home returned HTTP {response.status}")
        await page.wait_for_timeout(1500)
        gst = page.get_by_role("button", name="GST").first
        if await gst.count() and await gst.is_visible():
            await gst.click(force=True)
            await page.wait_for_timeout(700)

    async def _api_json(self, page, path: str):
        request = page.context.request
        last_error = None
        for attempt in range(1, 4):
            try:
                if not self._api_token:
                    token_response = await request.post(
                        urljoin(BASE_URL, "api/authenticate-token"), timeout=30000
                    )
                    if not token_response.ok:
                        raise RuntimeError(f"GST token HTTP {token_response.status}")
                    token_payload = await token_response.json()
                    self._api_token = str(token_payload.get("id_token") or "")
                    if not self._api_token:
                        raise RuntimeError("GST token response had no id_token")
                response = await request.get(
                    urljoin(BASE_URL, path.lstrip("/")),
                    headers={"Authorization": f"Bearer {self._api_token}",
                             "Accept": "application/json"},
                    timeout=60000,
                )
                if response.status == 401:
                    self._api_token = ""
                    raise RuntimeError("GST API token expired")
                if not response.ok:
                    body = (await response.text())[:160]
                    raise RuntimeError(f"{path} HTTP {response.status}: {body}")
                return await response.json()
            except Exception as error:
                last_error = error
                self._api_token = ""
                if attempt < 3:
                    await page.wait_for_timeout(1000 * attempt)
        raise last_error

    async def _discover_circulars(self, page, baseline: bool) -> GSTCollection:
        try:
            await self._click_tab(
                page, next(section for section in SECTIONS if section.name == "Circular")
            )
            items = await self._api_json(
                page, f"api/cbic-circular-msts/fetchAllCircularsByTaxId/{GST_TAX_ID}"
            )
            records: list[DocumentRecord] = []
            for item in items:
                if item.get("isActive") == "N":
                    continue
                path = (item.get("docFilePath") or "").replace("\\", "/").strip("/")
                if not path.lower().endswith(".pdf"):
                    continue
                pdf_url = urljoin(BASE_URL, "content/pdf/" + path)
                number = str(item.get("circularNo") or "").strip()
                name = str(item.get("circularName") or "").strip()
                title = " - ".join(value for value in (number, name) if value) or path.rsplit("/", 1)[-1]
                date = str(item.get("circularDt") or item.get("issueDt") or item.get("createdDt") or "")
                portal_category = str(item.get("circularCategory") or "").strip()
                portal_year = date[:4]
                records.append(DocumentRecord(
                    source="CBIC GST", category="Circular", record_date=date,
                    title=title, detail_url=pdf_url, document_urls=(pdf_url,),
                    metadata=(("portal_category", portal_category),
                              ("portal_year", portal_year)),
                ))
            return GSTCollection(records, 1, len(records), "playwright-api", True)
        except Exception as error:
            return GSTCollection([], 0, 0, "playwright-api", False, str(error))

    async def _discover_notifications(self, page, section: GSTSection, baseline: bool,
                                      max_pages: int, timeout: float) -> GSTCollection:
        try:
            await self._click_tab(page, section)
            pane = page.locator("body")
            category_select = await self._visible_locator(pane, section.category_id)
            year_select = await self._visible_locator(pane, section.year_id)
            if year_select is None:
                raise RuntimeError(f"{section.name}: year selector not found")
            years = await self._options(year_select)
            years = [
                (value, label) for value, label in years
                if label.isdigit() and int(label) >= self.from_year
            ]
            years.sort(key=lambda pair: int(pair[1]), reverse=True)
            if not baseline:
                years = years[:1]
            categories = await self._options(category_select) if category_select else [("", "All")]
            if not categories:
                categories = [("", "All")]
            records: list[DocumentRecord] = []
            pages_checked = 0
            raw_rows = 0
            started = monotonic()
            for category_value, category_label in categories:
                for year_value, year_label in years:
                    if monotonic() - started >= max(5.0, timeout):
                        return GSTCollection(records, pages_checked, raw_rows,
                                             "playwright-ui", False, "category timeout reached")
                    pane = page.locator("body")
                    category_select = await self._visible_locator(pane, section.category_id)
                    year_select = await self._visible_locator(pane, section.year_id)
                    if category_select and category_value:
                        await category_select.select_option(category_value)
                        await self._wait_for_table(pane)
                    if year_select:
                        await year_select.select_option(year_value)
                        await self._wait_for_table(pane)
                    await self._select_page_size_100(pane)
                    found, pages, rows, error = await self._collect_notification_rows(
                        page, pane, section, category_label, year_label, max_pages,
                    )
                    records.extend(found)
                    pages_checked += pages
                    raw_rows += rows
                    if error:
                        return GSTCollection(records, pages_checked, raw_rows,
                                             "playwright-ui", False, error)
            return GSTCollection(records, pages_checked, raw_rows,
                                 "playwright-ui", True)
        except Exception as error:
            return GSTCollection([], 0, 0, "playwright-ui", False, str(error))

    async def _collect_notification_rows(self, page, pane, section: GSTSection,
                                         category: str, year: str, max_pages: int):
        records: list[DocumentRecord] = []
        seen_pages: set[tuple[str, ...]] = set()
        pages = 0
        rows_seen = 0
        while pages < max_pages:
            await self._wait_for_table(pane)
            rows = pane.locator("table.table-hover:visible tbody tr")
            row_count = await rows.count()
            signature = await self._row_signature(rows)
            if signature in seen_pages:
                return records, pages, rows_seen, "GST UI returned a duplicate page"
            seen_pages.add(signature)
            pages += 1
            for index in range(row_count):
                row = rows.nth(index)
                cells = row.locator("td")
                if await cells.count() < 3:
                    continue
                number = (await cells.nth(0).inner_text()).strip()
                date = (await cells.nth(1).inner_text()).strip()
                subject = (await cells.nth(2).inner_text()).strip()
                english = row.locator('a[aria-label="English"]')
                if not await english.count():
                    english = row.get_by_text("English", exact=True)
                if not await english.count():
                    continue
                rows_seen += 1
                try:
                    # The portal normally exposes the viewer URL directly on
                    # the English anchor. Reading href avoids opening a
                    # browser popup for every row during a historical run.
                    popup_url = await english.first.get_attribute("href")
                    if popup_url and "/view-pdf/" in popup_url.lower():
                        popup_url = urljoin(page.url, popup_url)
                    else:
                        async with page.expect_popup(timeout=15000) as popup_info:
                            await english.first.click()
                        popup = await popup_info.value
                        popup_url = popup.url
                        await popup.close()
                    match = re.search(r"/view-pdf/(\d+)/ENG", popup_url)
                    if not match:
                        continue
                    detail = await self._api_json(
                        page, f"api/{section.endpoint}/{match.group(1)}"
                    )
                    path = (detail.get("docFilePath") or detail.get("contentFilePath") or "").replace("\\", "/").strip("/")
                    if not path.lower().endswith(".pdf"):
                        continue
                    pdf_url = urljoin(BASE_URL, "content/pdf/" + path)
                    records.append(DocumentRecord(
                        source="CBIC GST", category=section.name,
                        record_date=date, title=f"{number} - {subject}".strip(" -"),
                        detail_url=popup_url, document_urls=(pdf_url,),
                        metadata=(("portal_category", category),
                                  ("portal_year", year)),
                    ))
                except Exception:
                    continue
            next_control = await self._next_page_control(pane)
            if next_control is None:
                break
            old_signature = signature
            await next_control.click(force=True)
            changed = False
            for _ in range(30):
                await page.wait_for_timeout(300)
                candidate = await self._row_signature(rows)
                if candidate and candidate != old_signature:
                    changed = True
                    break
            if not changed:
                return records, pages, rows_seen, "GST UI pagination did not change"
        return records, pages, rows_seen, "" if pages < max_pages else ""

    @staticmethod
    async def _row_signature(rows) -> tuple[str, ...]:
        values = []
        for index in range(await rows.count()):
            values.append(await rows.nth(index).inner_text())
        return tuple(values)

    async def _click_tab(self, page, section: GSTSection) -> None:
        tab = page.locator(f"#{section.pane_id}-tab").first
        if not await tab.count() or not await tab.is_visible():
            tabs = page.get_by_text(section.tab_label, exact=True)
            visible = [item for item in await tabs.all() if await item.is_visible()]
            if not visible:
                raise RuntimeError(f"{section.tab_label} tab is not visible")
            tab = visible[-1]
        await tab.click(force=True)
        selector_ids = [section.category_id, section.year_id]
        for _ in range(60):
            if await self._visible_locator(page.locator("body"), section.year_id):
                return
            await page.wait_for_timeout(300)
        raise RuntimeError(
            f"{section.tab_label} controls did not load: {selector_ids}"
        )

    @staticmethod
    async def _options(select) -> list[tuple[str, str]]:
        result = []
        for option in await select.locator("option").all():
            value = await option.get_attribute("value")
            label = (await option.inner_text()).strip()
            if value:
                result.append((value, label))
        return result

    @staticmethod
    async def _visible_locator(container, selector):
        if not selector:
            return None
        for item in await container.locator(selector).all():
            if await item.is_visible():
                return item
        return None

    async def _wait_for_table(self, pane) -> None:
        rows = pane.locator("table.table-hover:visible tbody tr")
        previous = -1
        steady = 0
        for _ in range(40):
            count = await rows.count()
            if count == previous:
                steady += 1
                if steady >= 2:
                    return
            else:
                previous, steady = count, 0
            await pane.page.wait_for_timeout(300)

    async def _wait_for_table_result(self, pane) -> None:
        """Wait for rows/summary text to settle after a filter change."""
        previous = None
        steady = 0
        for _ in range(60):
            summary = await self._summary_text(pane)
            rows = pane.locator("table.table-hover:visible tbody tr")
            row_signature = await self._row_signature(rows)
            current = (summary, row_signature)
            if current == previous and (summary or row_signature):
                steady += 1
                if steady >= 2:
                    return
            else:
                previous, steady = current, 0
            await pane.page.wait_for_timeout(300)

    async def _summary_text(self, pane) -> str:
        pattern = re.compile(r"Showing\s+.*?\s+of\s+[\d,]+\s+(?:items|entries)", re.I | re.S)
        candidates = []
        for selector in (".dataTables_info", ".table-info", "div", "span", "p"):
            for item in await pane.locator(selector).all():
                if await item.is_visible():
                    text = re.sub(r"\s+", " ", (await item.inner_text()).strip())
                    if text and pattern.search(text):
                        candidates.append(text)
        return min(candidates, key=len) if candidates else ""

    async def _read_result_total(self, pane) -> int | None:
        text = await self._summary_text(pane)
        total = self._read_total_from_text(text)
        if total is not None:
            return total
        return 0 if await self._has_no_result_message(pane) else None

    @staticmethod
    async def _has_no_result_message(pane) -> bool:
        text = " ".join((await pane.inner_text()).split())
        return bool(re.search(
            r"(?:notification|circular)\s+not\s+available\s+for\s+selected\s+year",
            text, re.I,
        ))

    @staticmethod
    def _read_total_from_text(text: str) -> int | None:
        match = re.search(r"\bof\s+([\d,]+)\s+(?:items|entries)\b", text, re.I)
        return int(match.group(1).replace(",", "")) if match else None

    async def _validate_result_year(self, pane, selected_year: str, total: int) -> None:
        """Reject a stale table whose dates do not match the selected year."""
        if total == 0:
            return
        rows = pane.locator("table.table-hover:visible tbody tr")
        years: set[str] = set()
        for index in range(await rows.count()):
            cells = rows.nth(index).locator("td")
            if await cells.count() < 2:
                continue
            date_text = await cells.nth(1).inner_text()
            years.update(re.findall(r"\b(?:19|20)\d{2}\b", date_text))
        if years and years != {selected_year}:
            raise RuntimeError(
                f"stale portal result: selected year {selected_year}, "
                f"visible row years {sorted(years)}"
            )

    async def _select_page_size_100(self, pane) -> None:
        for select in await pane.locator("select").all():
            if not await select.is_visible():
                continue
            options = await self._options(select)
            if not any(value == "100" or label == "100" for value, label in options):
                continue
            try:
                await select.select_option(label="100")
            except Exception:
                await select.select_option("100")
            await self._wait_for_table(pane)
            return

    async def _next_page_control(self, pane):
        pagination = await self._visible_locator(pane, "ul.pagination")
        if pagination is None:
            pagination = await self._visible_locator(pane, ".dataTables_paginate")
        if pagination is None:
            return None
        for control in await pagination.locator("a, button").all():
            label = (await control.inner_text()).strip().lower()
            aria = (await control.get_attribute("aria-label") or "").lower()
            classes = (await control.get_attribute("class") or "").lower()
            parent_classes = (await control.locator("..").get_attribute("class") or "").lower()
            disabled = "disabled" in classes or "disabled" in parent_classes
            if ("next" in classes or "next" in aria or label in {">", "next", "next page", "»"}) and not disabled and await control.is_visible():
                return control
        return None

    async def _record_result(self, summary: dict, section: GSTSection,
                             result: GSTCollection, checked_at: str, *,
                             runtime: BrowserRuntime | None, download_new: bool,
                             download_dir: Path | None,
                             download_existing: bool) -> None:
        new_count = downloaded = download_errors = 0
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
                count, errors = await self._download_documents(record, runtime, download_dir)
                downloaded += count
                download_errors += errors

        status = "ok" if not result.error else ("partial" if result.records else "failed")
        if result.error:
            self.state.audit(AuditEvent(
                fingerprint=f"CBIC GST|{section.name}|{checked_at}",
                source="CBIC GST", category=section.name, record_date="", title="",
                detail_url=BASE_URL,
                event="CHECK_PARTIAL" if result.records else "CHECK_FAILED",
                occurred_at=checked_at, transport=result.transport, message=result.error,
            ))
        summary["categories"][section.name] = {
            "status": status,
            "records_checked": len(result.records),
            "new_records": new_count,
            "pages_checked": result.pages_checked,
            "raw_rows_seen": result.raw_rows_seen,
            "duplicate_rows": result.raw_rows_seen - len(result.records),
            "complete": result.complete and not result.error,
            "transport": result.transport,
            "documents_downloaded": downloaded,
            "document_errors": download_errors,
        }
        if result.error:
            summary["categories"][section.name]["error"] = result.error

    async def _download_documents(self, record: DocumentRecord,
                                  runtime: BrowserRuntime | None,
                                  download_dir: Path | None) -> tuple[int, int]:
        root = (download_dir or Path("data") / "gst_documents") / record.category
        downloaded = errors = 0
        for index, url in enumerate(record.document_urls, 1):
            try:
                data = None
                transport = ""
                if runtime is not None and runtime.context is not None:
                    token_response = await runtime.context.request.post(
                        urljoin(BASE_URL, "api/authenticate-token"), timeout=30000
                    )
                    token_payload = await token_response.json()
                    response = await runtime.context.request.get(
                        url,
                        headers={"Authorization": f"Bearer {token_payload['id_token']}"},
                        timeout=30000,
                    )
                    if not response.ok:
                        raise RuntimeError(f"HTTP {response.status} while downloading PDF")
                    data, transport = await response.body(), "playwright-request"
                else:
                    for name, fetcher in (("httpx", fetch_bytes_httpx), ("curl_cffi", fetch_bytes_curl)):
                        try:
                            data = await asyncio.wait_for(fetcher(url), timeout=35.0)
                            transport = name
                            break
                        except Exception:
                            continue
                if not data or not data.startswith(b"%PDF"):
                    try:
                        payload = json.loads(data.decode("utf-8"))
                        encoded = payload.get("data") if isinstance(payload, dict) else None
                        data = base64.b64decode(encoded) if encoded else b""
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                        data = b""
                if not data.startswith(b"%PDF"):
                    raise RuntimeError("response was not a PDF or base64 PDF payload")
                root.mkdir(parents=True, exist_ok=True)
                suffix = "" if index == 1 else f"_{index:02d}"
                path = root / self._filename(record, suffix)
                path.write_bytes(data)
                self.state.audit(AuditEvent(
                    fingerprint=record.fingerprint, source=record.source,
                    category=record.category, record_date=record.record_date,
                    title=record.title, detail_url=record.detail_url,
                    event="DOCUMENT_DOWNLOADED", occurred_at=utc_now(),
                    document_url=url, file_path=str(path.resolve()),
                    file_sha256=sha256_bytes(data), transport=transport,
                ))
                downloaded += 1
            except Exception as error:
                errors += 1
                self.state.audit(AuditEvent(
                    fingerprint=record.fingerprint, source=record.source,
                    category=record.category, record_date=record.record_date,
                    title=record.title, detail_url=record.detail_url,
                    event="DOCUMENT_FAILED", occurred_at=utc_now(),
                    document_url=url, message=str(error),
                ))
        return downloaded, errors

    @staticmethod
    def _filename(record: DocumentRecord, suffix: str) -> str:
        title = re.sub(r"\s+", " ", record.title).strip()
        title = re.sub(r'[<>:"/\\|?*]', "_", title)[:150] or "GST_document"
        return f"{title}_{record.fingerprint[:10]}{suffix}.pdf"

    def close(self) -> None:
        self.state.close()
