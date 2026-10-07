# GST Acknowledgment Presigned URL API

## Start the API

```bash
docker compose up --build
```

Swagger UI:

```text
http://192.168.10.100:8001/docs
```

## Endpoint

```http
GET /acknowledgments/presigned-url
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
http://192.168.10.100:8001/acknowledgments/presigned-url?company=zetwerk&state=Andhra%20Pradesh&district=Vizianagaram&month=august&financial_year=2026-27&form=gstr-1
```

The API performs these steps:

1. Reads the matching company, state, district, month, and financial-year record from PostgreSQL.
2. Finds the selected form in the JSON `form` list.
3. Retrieves its folder from the JSON `s3_path` list.
4. Lists PDF files under that SeaweedFS folder.
5. Returns each matching PDF's file name and presigned URL.

Example response:

```json
{
  "files": [
    {
      "file_name": "GSTR1_37AABCZ1506C1ZQ_082026.pdf",
      "url": "temporary SeaweedFS presigned URL"
    }
  ],
  "expires_in": 3600
}
```

## S3 folder structure

```text
compliance/zetwerk/{state}/2026/{month}/{form}/{PDF file}
```

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
