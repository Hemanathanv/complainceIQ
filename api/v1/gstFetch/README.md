# GST Fetch API

Versioned FastAPI routes for the `"Automation"."gstFetch"` table.

## Configuration

Configure the repository root `.env` with the shared database settings:

```env
TZ=Asia/Kolkata
PGTZ=Asia/Kolkata
DATABASE_URL=postgresql://ewms-postgres:<url-encoded-password>@postgres:5432/complaince-iq?schema=Automation
DB_HOST=postgres
DB_PORT=5432
DB_NAME=complaince-iq
DB_USER=ewms-postgres
DB_PASSWORD=your-password
```

The application loads `.env` automatically and connects through one shared
asynchronous Prisma client. Configure `DATABASE_URL` for both API services.
The public API requires frontend bearer tokens; the worker calls the
unpublished `complaince-api-internal` service.

## Install dependencies

```powershell
cd <repository-root>
python -m pip install -r requirements.txt
```

## Start the API

```powershell
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

## Endpoints

Health check:

```text
GET http://localhost:8000/api/v1/health
```

List active synced record IDs and GSTINs for the Temporal workflow:

```text
GET http://localhost:8000/api/v1/gst-cache/records
```

Persist up to four bot results through the internal API in a single database
transaction (public API does not expose this route):

```text
POST http://complaince-api-internal:8000/api/v1/internal/gst-cache/batch
```

Each result has an `id` and a `gst_data` object. The worker keeps results in
memory for batches of four and emits the scraper result on stdout, so this
workflow does not create per-GSTIN JSON result files. The internal API validates
each result and updates the corresponding `"Automation"."gstFetch"` rows in
one Prisma transaction.

The worker first calls `POST /api/v1/internal/gst-cache/sync` to upsert all GSTIN
secrets from its configured Infisical project. This route is mounted only on
the internal API service. Public API requests use a bearer token obtained
through `/api/v1/auth/token`; see the root README for provisioning and refresh.

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

Get a row by UUID, state, and district:

```text
GET http://localhost:8000/api/v1/gst-cache/{uuid}/{state_name}/{district_name}
```

Example:

```text
GET http://localhost:8000/api/v1/gst-cache/dc4f22aa-4682-436c-a355-743971060d2c/Rajasthan/Kota
```

Get all full rows by state and district:

```text
GET http://localhost:8000/api/v1/gst-cache/Rajasthan/Kota
```

Supported column names include `id`, `gstin`, `business_info`, `filing_tables`, `legal_name`, `status`, `fetched_at`, `fetch_count`, `created_at`, `state_name`, and `district_name`.

Interactive API documentation:

```text
http://localhost:8000/docs
```

## Docker Compose

Build the API image manually from the repository root, then start it with the
root compose file:

```bash
docker build -f Dockerfile.api -t complaince-iq-api:manual .
docker compose up -d complaince-api
```

View logs:

```powershell
docker compose logs -f complaince-api
```

Stop the service:

```powershell
docker compose down
```

By default, the public API app is available on host port `8020`. Set `API_PORT`
in `.env` to use another host port:

```env
API_PORT=8001
```
