# Compliance Scrapper v2

## Setup with uv

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first, then run from this folder:

```powershell
uv venv
uv sync
uv run playwright install chromium
```

Activate the environment when needed:

```powershell
.\.venv\Scripts\Activate.ps1
```

Copy `.env.example` to `.env` and adjust the download path or scraper settings before running a scraper.

Example:

```powershell
uv run python ".\004 GST Act.py"
```

## Built-in resilience

Each scraper is self-contained and can still be run directly. Every existing
scraper now writes `logs/run_status.json` and `logs/runtime.log` inside its own
output folder, retries an unhandled failure up to three times with exponential
backoff and jitter, delays document requests, detects HTTP 403/429 responses,
and uses a longer cooldown when access blocking or throttling is detected.

These settings are read from `.env`:

```text
SCRAPER_MAX_ATTEMPTS=3
SCRAPER_REQUEST_DELAY_SECONDS=1.0
SCRAPER_BLOCK_COOLDOWN_SECONDS=300
```

The scripts do not bypass CAPTCHA or access controls. A detected block is
recorded and retried only after the configured cooldown.
