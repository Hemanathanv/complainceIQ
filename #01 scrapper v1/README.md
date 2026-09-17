# Compliance Scrapper v1

## Setup with uv

From this folder, create and synchronize the environment:

```powershell
uv venv
uv sync
uv run playwright install chromium
```

Activate the environment when needed:

```powershell
.\.venv\Scripts\Activate.ps1
```

Example:

```powershell
uv run python ".\sebi.py"
```
