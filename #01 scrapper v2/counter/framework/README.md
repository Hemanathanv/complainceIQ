# Shared monitoring framework

Reusable runtime, normalized records, state, and audit storage live here.
Each monitored website gets its own adapter folder, such as `sebi/`.

The run history, current completion state, failures, and recovery plan are in
[AUDIT_LOG.md](AUDIT_LOG.md).

## SEBI production commands — Circulars and Gazette Notifications

Run these commands from the `counter` folder.

### Continuous monitoring

```powershell
uv run python -m framework.sebi.run --pages 1
```

This checks the newest page of SEBI Circulars and Gazette Notifications. It uses
Playwright first, then `httpx`, then `curl_cffi` if the browser cannot read a
listing. Scrapling normalizes the HTML from every transport. SQLite fingerprints
and audits every observed record, so repeating the command is safe.

To keep the process running and check every five minutes:

```powershell
uv run python -m framework.sebi.run --watch --interval 300 --download
```

`--download` follows a newly discovered record's detail page, finds PDF links,
downloads each PDF once, and records its path and SHA-256 in the SQLite audit
trail. Without this flag the run only discovers and audits listing records.

### Historical reconciliation

```powershell
uv run python -m framework.sebi.run --baseline
```

This performs a bounded historical scan. It is intentionally separate from the
monitor because SEBI's client-side pagination endpoint can stall or return a
duplicate or shifting pages. The scan records a `coverage_warning` when raw
rows contain duplicate URLs; its `new_records` value means newly observed by
that reconciliation, not confirmed newly uploaded by SEBI. It records
`CHECK_PARTIAL`/`CHECK_FAILED` in the audit trail and returns instead of hanging
forever. Do not use a full reconciliation to trigger downloads:

```powershell
uv run python -m framework.sebi.run --baseline --max-pages 120 --baseline-timeout 1800
```

The framework discovers both listing categories and can download their linked
PDF documents with `--download`. Document downloading is kept in the separate
source folder described below.

## GST ACT: Circulars and Notifications only

The GST adapter intentionally excludes Acts, Rules, Forms, Instructions,
Guidelines, and Orders.

Monitor the newest GST ACT year:

```powershell
uv run python -m framework.gst.run --watch --interval 300 --pages 1 --download
```

Create the initial GST baseline across available years/categories:

```powershell
uv run python -m framework.gst.run --baseline --from-year 2017 --max-pages 20 --baseline-timeout 1800
```

To get authoritative category/year totals, read the totals rendered by the
portal itself (for example, `Circulars CGST` → `2024`):

```powershell
uv run python -m framework.gst.run --counts --from-year 2017 --count-timeout 900
```

This count report does not count files in an old output folder. It reads the
portal's `Showing ... of ... items` result for every visible filter and marks
the report incomplete if any filter total cannot be read.

GST state and PDFs are stored separately under `data\gst_state.sqlite3` and
`data\gst_documents`.

## Income Tax Act 2025: Circulars and Notifications only

The Income Tax adapter dynamically follows the listing pagination and reads
the portal's current total. It excludes Acts and every other section.

Monitor the newest page:

```powershell
uv run python -m framework.income_tax.run --watch --interval 300 --pages 1 --download
```

Run a bounded historical reconciliation:

```powershell
uv run python -m framework.income_tax.run --baseline --max-pages 1500 --baseline-timeout 1800
```

## Provident Fund Act 1952: Circulars only

The Provident Fund Act 1952 adapter follows the live circular cards and
client-side `Next »` pagination. It excludes the EPF Act page and all other
Provident Fund sections.

Monitor the newest page:

```powershell
uv run python -m framework.epfo.run --watch --interval 300 --pages 1 --download --headed
```

Run a historical reconciliation across the current 95-page listing:

```powershell
uv run python -m framework.epfo.run --baseline --max-pages 100 --baseline-timeout 1800 --headed
```

## ESI Act 1948: Circulars only

The ESI Act 1948 adapter reads the live circular table, follows its pagination, and
captures every PDF link present in a subject cell. Each record also stores the
branch, S.No., dated value, publish date, console serial number, and the site's
`Last updated / Reviewed` date.

Monitor the newest page:

```powershell
uv run python -m framework.esic.run --watch --interval 300 --pages 1 --download --headed
```

Run a historical reconciliation across the current listing:

```powershell
uv run python -m framework.esic.run --baseline --max-pages 100 --baseline-timeout 1800 --headed
```

## WCD: Orders and Notices plus Notifications

The WCD adapter monitors the current Ministry of Women and Child Development
document lists. Each record stores the title, published date, document type,
file size, and every PDF link in the card. Archive pages are intentionally not
included in the current-update monitor.

Monitor the current lists:

```powershell
uv run python -m framework.wcd.run --watch --interval 300 --pages 1 --download --headed
```

Create a current-list baseline:

```powershell
uv run python -m framework.wcd.run --baseline --headed
```

## CPCB: Current Circulars, Office Orders, and Memoranda

The CPCB adapter reads the live DataTables listing and captures S.No.,
division, title, date of issue, row category, PDF type, file size, and the
PDF URL. It also records the page's `Updated On` value. Monitor mode checks the
newest 10-row view; baseline mode selects `All` and reconciles every current
entry.

Monitor the newest entries:

```powershell
uv run python -m framework.cpcb.run --watch --interval 300 --pages 1 --download --headed
```

Create a current-list baseline:

```powershell
uv run python -m framework.cpcb.run --baseline --headed
```

## Ministry of Labour: Orders/Notices and Gazette Notifications

The Labour adapter selects English and monitors the current Orders and Notices
and Gazettes Notifications sections. It follows Orders/Notices `VIEW ALL`
category links dynamically, captures direct and category documents, and stores
title, published date, PDF/Visit type, file size, URL, section, subcategory,
and the ministry's last-updated date.

Monitor the newest pages:

```powershell
uv run python -m framework.labour.run --watch --interval 300 --pages 1 --download --headed
```

Create the initial current-section baseline:

```powershell
uv run python -m framework.labour.run --baseline --max-pages 10 --headed
```

The Labour portal is protected by an Akamai/WAF layer. The adapter therefore
paces navigations with a small random pause, performs the language handshake once per browser session,
and stops the complete run on HTTP 403/429 instead of continuing into the
other section. A blocked section is reported as `blocked`; no retry or anti-bot
bypass is attempted. The pacing can be adjusted explicitly with
`--request-delay` and `--request-jitter`.

After a temporary block clears, a section can be baselined independently:

```powershell
uv run python -m framework.labour.run --baseline --section "Gazettes Notifications" --max-pages 10 --headed
```

## PowerShell: baseline, download, and retry

Run these commands from the `counter` folder. They write documents under
`counter\data\download\<source>` and append console output to the matching
log under `counter\data\download\logs`.

```powershell
$counter = 'C:\Users\User\Desktop\complaince bot code\#01 scrapper v2\counter'
Set-Location -LiteralPath $counter
$download = Join-Path $counter 'data\download'
$logs = Join-Path $download 'logs'
New-Item -ItemType Directory -Force -Path $download, $logs | Out-Null
```

### First full baseline plus download

Run the commands one at a time. These are intended for the first run when the
source state is empty or when only newly discovered records should be
downloaded.

```powershell
& uv run python -m framework.cpcb.run --baseline --max-pages 100 --baseline-timeout 900 --download --download-dir (Join-Path $download 'cpcb') *>&1 | Tee-Object (Join-Path $logs 'cpcb_baseline.log')
& uv run python -m framework.epfo.run --baseline --max-pages 100 --baseline-timeout 1800 --download --download-dir (Join-Path $download 'epfo') *>&1 | Tee-Object (Join-Path $logs 'epfo_baseline.log')
& uv run python -m framework.esic.run --baseline --max-pages 100 --baseline-timeout 1800 --download --download-dir (Join-Path $download 'esic') *>&1 | Tee-Object (Join-Path $logs 'esic_baseline.log')
& uv run python -m framework.gst.run --baseline --from-year 2017 --max-pages 20 --baseline-timeout 1800 --download --download-dir (Join-Path $download 'gst') --headed *>&1 | Tee-Object (Join-Path $logs 'gst_baseline.log')
& uv run python -m framework.income_tax.run --baseline --max-pages 1500 --baseline-timeout 1800 --download --download-dir (Join-Path $download 'income_tax') *>&1 | Tee-Object (Join-Path $logs 'income_tax_baseline.log')
& uv run python -m framework.labour.run --baseline --section 'Orders and Notices' --max-pages 10 --baseline-timeout 1800 --download --download-dir (Join-Path $download 'labour_orders') --headed *>&1 | Tee-Object (Join-Path $logs 'labour_orders_baseline.log')
& uv run python -m framework.labour.run --baseline --section 'Gazettes Notifications' --max-pages 10 --baseline-timeout 1800 --download --download-dir (Join-Path $download 'labour_gazettes') --headed *>&1 | Tee-Object (Join-Path $logs 'labour_gazettes_baseline.log')
& uv run python -m framework.sebi.run --baseline --max-pages 120 --baseline-timeout 1800 --download --download-dir (Join-Path $download 'sebi') *>&1 | Tee-Object (Join-Path $logs 'sebi_baseline.log')
& uv run python -m framework.wcd.run --baseline --max-pages 100 --baseline-timeout 900 --download --download-dir (Join-Path $download 'wcd') *>&1 | Tee-Object (Join-Path $logs 'wcd_baseline.log')
```

### Retry or resume pending downloads

Use the same source-specific command after an interrupted, blocked, or partial
run. `--download-existing` revisits already recorded rows and retries their
document links. Filenames are deterministic, so successful files are replaced
in place rather than duplicated.

```powershell
& uv run python -m framework.cpcb.run --baseline --max-pages 100 --baseline-timeout 900 --download --download-existing --download-dir (Join-Path $download 'cpcb') *>&1 | Tee-Object (Join-Path $logs 'cpcb_retry.log')
& uv run python -m framework.epfo.run --baseline --max-pages 100 --baseline-timeout 1800 --download --download-existing --download-dir (Join-Path $download 'epfo') *>&1 | Tee-Object (Join-Path $logs 'epfo_retry.log')
& uv run python -m framework.esic.run --baseline --max-pages 100 --baseline-timeout 1800 --download --download-existing --download-dir (Join-Path $download 'esic') *>&1 | Tee-Object (Join-Path $logs 'esic_retry.log')
& uv run python -m framework.gst.run --baseline --from-year 2017 --max-pages 20 --baseline-timeout 1800 --download --download-existing --download-dir (Join-Path $download 'gst') --headed *>&1 | Tee-Object (Join-Path $logs 'gst_retry.log')
& uv run python -m framework.income_tax.run --baseline --max-pages 1500 --baseline-timeout 1800 --download --download-existing --download-dir (Join-Path $download 'income_tax') *>&1 | Tee-Object (Join-Path $logs 'income_tax_retry.log')
& uv run python -m framework.labour.run --baseline --section 'Orders and Notices' --max-pages 10 --baseline-timeout 1800 --download --download-existing --download-dir (Join-Path $download 'labour_orders') --headed *>&1 | Tee-Object (Join-Path $logs 'labour_orders_retry.log')
& uv run python -m framework.labour.run --baseline --section 'Gazettes Notifications' --max-pages 10 --baseline-timeout 1800 --download --download-existing --download-dir (Join-Path $download 'labour_gazettes') --headed *>&1 | Tee-Object (Join-Path $logs 'labour_gazettes_retry.log')
& uv run python -m framework.sebi.run --baseline --max-pages 120 --baseline-timeout 1800 --download --download-existing --download-dir (Join-Path $download 'sebi') *>&1 | Tee-Object (Join-Path $logs 'sebi_retry.log')
& uv run python -m framework.wcd.run --baseline --max-pages 100 --baseline-timeout 900 --download --download-existing --download-dir (Join-Path $download 'wcd') *>&1 | Tee-Object (Join-Path $logs 'wcd_retry.log')
```

### Newest-page monitoring with download

For recurring monitoring, replace `--baseline` with `--pages 1` and omit
`--download-existing`; normal monitor runs download only newly discovered
records.

```powershell
& uv run python -m framework.cpcb.run --pages 1 --download --download-dir (Join-Path $download 'cpcb')
& uv run python -m framework.epfo.run --pages 1 --download --download-dir (Join-Path $download 'epfo')
& uv run python -m framework.esic.run --pages 1 --download --download-dir (Join-Path $download 'esic')
& uv run python -m framework.gst.run --pages 1 --download --download-dir (Join-Path $download 'gst') --headed
& uv run python -m framework.income_tax.run --pages 1 --download --download-dir (Join-Path $download 'income_tax')
& uv run python -m framework.labour.run --pages 1 --download --download-dir (Join-Path $download 'labour') --headed
& uv run python -m framework.sebi.run --pages 1 --download --download-dir (Join-Path $download 'sebi')
& uv run python -m framework.wcd.run --pages 1 --download --download-dir (Join-Path $download 'wcd')
```
