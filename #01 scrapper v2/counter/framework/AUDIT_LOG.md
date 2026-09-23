# Scraper audit log

Audit window: 22 September 2026 night through 23 September 2026 09:53 IST  
Download root: `counter\data\download\`  
Log root: `counter\data\download\logs\`

This file records the actual runs, not planned commands. A source is marked
complete only when its final run reports `complete: true` and its download
errors are reviewed.

## Current status

| Source | Scope attempted | Result | PDFs currently in folder | State |
|---|---|---:|---:|---|
| CPCB | Current Circulars / Office Orders / Memoranda | 28 records, 28 PDFs, 0 errors | 28 | Complete |
| Provident Fund Act 1952 | 1,884 circular records | 1,884/1,884 records, 1,870 PDFs downloaded, 14 rows without usable PDF URLs | 1,870 | Complete with source-link gaps |
| ESI Act 1948 | 735 circular records across 74 pages | 735/735 records, 1,043 PDFs, retry completed successfully | 1,043 | Complete |
| GST ACT | Notifications and Circulars from 2017 onward | No records or PDFs; year selector/API fetch failed | 0 | Failed; pending portal retry |
| Income Tax Act 2025 | Circulars and Notifications | HTTP 403 for both sections | 0 | Failed; pending retry |
| Labour | Orders/Notices and Gazette Notifications | Not started | 0 | Pending |
| SEBI | Circulars and Gazette Notifications | Circular download was stopped before the Gazette pass after a scope misunderstanding | 4,010 Circular PDFs, 0 Gazette PDFs | Pending resume for both categories |
| WCD | Orders and Notices plus Notifications | 11 records, 11 PDFs, 0 errors | 11 | Complete |

Current downloaded-file total is **6,962 PDFs** across CPCB, Provident Fund Act 1952, ESI Act 1948, SEBI, and WCD.
The SEBI count is live and will change until the resume process finishes.

## Run history

### CPCB

- The first attempt found all 28 rows but failed all 28 downloads because CPCB
  wraps PDF bytes inside an HTML document while declaring `application/pdf`.
- The adapter was corrected to extract the `%PDF` through `%%EOF` byte range.
- Retest completed successfully: **28 downloaded, 0 errors**.
- Evidence: `logs\cpcb_retest.log`.

### Provident Fund Act 1952

- Standalone Camoufox collection completed: **1,884/1,884 records**, zero rows
  skipped, and the portal reported 1,884 circulars.
- Download run completed with **1,870 PDFs downloaded** and **14 source rows
  without usable PDF URLs** (`http://false`/empty link on the portal).
- Evidence: `data\download\epfo\Circular` and `data\epfo_state.sqlite3`.

### ESI Act 1948

- Full baseline completed: **735 records, 74 pages, 1,043 linked PDFs**.
- One large 7 MB PDF returned HTTP 200 but its response was aborted while the
  body was being read. The document was subsequently redownloaded successfully
  and verified with a valid `%PDF` header.
- Evidence: `logs\esic_run.log`; the failed URL and error are recorded in
  `data\esic_state.sqlite3` as `DOCUMENT_FAILED`.

### GST ACT

- The initial headless attempt did not progress, so it was stopped safely.
- The headed retry then failed before collection: the Notification year
  selector was not found and the Circular API returned `TypeError: Failed to
  fetch`.
- No GST ACT records or PDFs were written by these runs.
- Evidence: `logs\gst_headed_run.log`.

### Income Tax Act 2025

- Both Circulars and Notifications returned **HTTP 403** at the listing page.
- No records or PDFs were written.
- Evidence: `logs\income_tax_run.log`.

### WCD

- Current baseline completed successfully:
  - Orders and Notices: 8 PDFs
  - Notifications: 3 PDFs
- Evidence: `logs\wcd_run.log`.

### SEBI — Circulars and Gazette Notifications

- The required scope is both categories: approximately **2,803 Circulars plus
  34 Gazette Notifications = 2,837 records**.
- The earlier no-download monitor checked page 1 only: 25 Circulars plus 25
  Gazette Notifications, 50 records total.
- The first bounded historical download run reported:
  - Circulars: 2,773 records checked of 2,802; 113 pages; 29 duplicate rows;
    1,461 PDFs downloaded; incomplete.
  - Gazette Notifications: DNS resolution failure; not collected.
- A resume run was started with `--download-existing` and stopped before the
  Gazette pass. The Circular folder currently contains about **4,010 PDFs**.
  This is greater than 2,803 records because some records expose multiple PDF
  attachments and retries revisit failed links.
- Gazette PDFs currently contain **0** files and must be collected in the next
  two-category resume run.
- Evidence: `logs\sebi_run.log`; current resume output is being written to
  `logs\sebi_resume_20260923.log` when the process emits its final result.

## Failure-handling plan

1. Resume SEBI with both Circulars and Gazette Notifications. Review duplicate pagination,
   `DOCUMENT_FAILED`, and `DOCUMENT_NOT_FOUND` values before marking it
   complete. Keep the existing deterministic filenames and validate every saved
   file as a PDF.
2. Retry Income Tax Act 2025 after the 403 block clears. The framework stops on
   403/429 and does not attempt an anti-bot bypass.
3. Retry GST ACT in headed mode after a fresh portal session. Confirm the year
   selector and API fetch before starting the historical download.
5. Run Labour as two isolated commands—Orders and Notices, then Gazette
   Notifications—with the configured delay/jitter so a block in one section
   does not hide the result of the other.
5. After each retry, verify the source log, SQLite audit events, folder count,
   `%PDF` header, and `%%EOF` trailer. Only then mark that source complete.

## Standard retry command

From the `counter` folder, use the source-specific retry commands in
`README.md`. The common retry flags are:

```powershell
--baseline --download --download-existing --download-dir <source-folder>
```

These retry already recorded rows and write the result to the source log under
`data\download\logs`.
