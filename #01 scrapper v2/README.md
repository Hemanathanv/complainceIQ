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
