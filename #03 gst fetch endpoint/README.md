# GST Fetch API

Versioned FastAPI service for reading records from `gstfetch.gstin_cache`.

## Configuration

Create a `.env` file in this project folder:

```env
TZ=Asia/Kolkata
PGTZ=Asia/Kolkata
DB_HOST=192.168.10.100
DB_PORT=5432
DB_NAME=complaince
DB_USER=postgres
DB_PASSWORD=your-password
```

The application loads `.env` automatically and connects to PostgreSQL when an API request is made.

## Install dependencies

```powershell
cd "C:\Users\User\Desktop\complaince bot code\#03 gst fetch endpoint"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Start the API

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Endpoints

Health check:

```text
GET http://localhost:8000/health
```

Get a complete row by UUID:

```text
GET http://localhost:8000/api/v1/gst-cache/{uuid}
```

Get one column by UUID and column name:

```text
GET http://localhost:8000/api/v1/gst-cache/{uuid}/{column_name}
```

Example:

```text
GET http://localhost:8000/api/v1/gst-cache/dc4f22aa-4682-436c-a355-743971060d2c/legal_name
```

Supported column names include `id`, `gstin`, `business_info`, `filing_tables`, `legal_name`, `status`, `fetched_at`, `fetch_count`, and `created_at`.

Interactive API documentation:

```text
http://localhost:8000/docs
```
