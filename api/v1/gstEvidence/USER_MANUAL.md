# GST Acknowledgment Presigned URL API

## Start the API

```bash
cd <repository-root>
docker build -f Dockerfile.api -t complaince-iq-api:manual .
docker compose up -d complaince-api
```

Swagger UI:

```text
http://localhost:8020/docs
```

## Endpoint

```http
GET /api/v1/gstEvidence/presigned-url
```

Required query arguments:

| Argument | Example |
| --- | --- |
| `company` | `zetwerk` |
| `state` | `Andhra Pradesh` |
| `district` | `Vizianagaram` |
| `month` | `august` |
| `financial_year` | `2026-27` |
| `form` | `gstr-1` |

Optional:

| Argument | Default | Purpose |
| --- | --- | --- |
| `expires_in` | `3600` | Presigned URL lifetime in seconds, from `1` through `604800`. |

## Working example

```text
http://localhost:8020/api/v1/gstEvidence/presigned-url?company=zetwerk&state=Andhra%20Pradesh&district=Vizianagaram&month=august&financial_year=2026-27&form=gstr-1
```

The API performs these steps:

1. Reads the matching company, state, district, month, and financial-year record from PostgreSQL.
2. Finds the selected form in the JSON `form` list.
3. Retrieves its folder from the JSON `s3_path` list.
4. Lists PDF files under that AWS S3 key prefix.
5. Returns each matching PDF's file name and presigned URL.

Example response:

```json
{
  "files": [
    {
      "file_name": "GSTR1_37AABCZ1506C1ZQ_082026.pdf",
      "url": "temporary AWS S3 presigned URL"
    }
  ],
  "expires_in": 3600
}
```

## S3 folder structure

```text
{S3_KEY_PREFIX}/gstEvidence/2026/04/37AABCZ1506C1ZQ/GSTR1_37AABCZ1506C1ZQ_202604_(2026-10-08).pdf
```

PDFs downloaded by the scheduled `worker/gstEvidence/gstEvidence.py` worker are
uploaded to AWS S3 under the return period year, month, and GSTIN. The filename
includes the GSTIN, return period, and upload date in Asia/Kolkata. Temporary local
downloads are removed after each Temporal activity. The S3 client uses boto3's
standard AWS credential chain (environment credentials, profile, or IAM role)
with `S3_REGION`, `S3_DEFAULT_BUCKET`, and `S3_KEY_PREFIX` from the root `.env`.
The worker skips a return month when that GSTIN already has a PDF in its S3
folder.

The worker reads JSON secrets from Infisical Production `/GST-CRED`. Each value
must contain `gst_no`, `user_name`, and `password`. The independent Temporal
schedule `gst-evidence-daily-schedule` starts at 13:00 Asia/Kolkata each day;
overlapping runs are skipped. Revoking the worker's Infisical access stops new
credential retrieval. Passwords are not placed in Temporal workflow history.

## Test script

```powershell
python .\test_api_list_pdfs.py
```

Use different values when needed:

```powershell
python .\test_api_list_pdfs.py `
  --company zetwerk `
  --state "Andhra Pradesh" `
  --district Vizianagaram `
  --month august `
  --financial-year 2026-27 `
  --form gstr-1
```
