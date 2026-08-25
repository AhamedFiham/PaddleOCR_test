# Invoice OCR Extraction

Extracts structured data from invoice images and PDFs with PaddleOCR,
returns a draft for a human to verify, and saves the confirmed record to
MySQL.

## Requirements

Python **3.12**. Do not use 3.14+ — paddlepaddle publishes no wheels for
it and the install fails with `Could not find a version that satisfies the
requirement paddlepaddle (from versions: none)`.

```powershell
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Running

```powershell
uvicorn main:app --reload
```

Then open <http://127.0.0.1:8000/docs> and drive the flow from Swagger UI.

The service starts even when MySQL is unreachable — `/invoices/extract`
has no database dependency, so the OCR half stays usable. Only the write
endpoints fail until a database is configured.

## Flow

Two phases, deliberately. Raw OCR output is never written to the database.

1. `POST /invoices/extract` — upload a PDF or image. Returns the extracted
   fields, a `field_confidence` score per field, the stored file path and
   the full raw OCR output. **Nothing is persisted.**
2. `POST /invoices` — submit the confirmed (possibly edited) fields. This
   writes the record and its line items to MySQL with `status="verified"`.
3. `GET /invoices/{id}` — read a saved record back.

## Database

Configured entirely through `DATABASE_URL`; nothing is hardcoded.

```powershell
$env:DATABASE_URL = "mysql+pymysql://USER:PASS@HOST:3306/DBNAME?charset=utf8mb4"
```

In PyCharm: Run → Edit Configurations → Environment variables.

The schema must already exist and be empty. `Base.metadata.create_all`
creates the two tables (`invoices`, `invoice_line_items`) on first run, but
it does not create the database itself.

## Deploying to a server

The front end is deployed separately, so it calls this API cross-origin.

```bash
cp .env.example .env      # then edit it
docker compose up -d --build
```

`docker-compose.yml` runs three containers: the API, a MySQL 8 database,
and an nginx reverse proxy on port 80.

Two settings in `.env` matter, and the service will refuse to start
without them:

- `MYSQL_PASSWORD` — root password for the bundled database.
- `CORS_ORIGINS` — the **browser origin the front end is served from**,
  e.g. `https://invoices.example.com`. Not the API's own URL. Scheme,
  host and port must match exactly, with no trailing slash.

### What the front-end developer needs

- **The API base URL**, e.g. `https://invoice-api.example.com`. Endpoints
  are `POST /invoices/extract`, `POST /invoices`, `GET /invoices/{id}`.
- **Their origin added to `CORS_ORIGINS`** before anything will work from
  a browser. A missing origin fails as an opaque CORS error, not a 4xx.
- **HTTPS on this API if their site is HTTPS.** Browsers block an HTTPS
  page from calling an HTTP endpoint, and no CORS setting overrides that.
  Terminate TLS at the proxy (or put this behind an existing load
  balancer) before integration.
- **A request timeout above 300s.** A single extract takes 100-250s on
  CPU. Their HTTP client needs a raised timeout, and any CDN or load
  balancer in front of this needs one too — nginx here is already set to
  600s. This is the single most likely integration failure.
- **`field_confidence`** is returned alongside the fields, scored 0-1 per
  field. Anything below ~0.7 is worth flagging in the review UI rather
  than presenting as trustworthy.

## Notes

- **oneDNN must stay disabled.** `PaddleOCR(..., enable_mkldnn=False)` in
  [paddleOcr.py](paddleOcr.py) is required, not a tuning choice: with it
  enabled, paddlepaddle 3.3.1 aborts text detection on this platform with
  `ConvertPirAttribute2RuntimeAttribute not support
  [pir::ArrayAttribute<pir::DoubleAttribute>]`.
- **The OCR engine is built once**, at app startup, and shared. Never
  construct `PaddleOCR(...)` inside a request handler.
- Reading order is reconstructed from detection geometry, because raw
  PaddleOCR order interleaves columns and separates labels from values.
- Detections below 0.5 confidence are dropped as noise.
- Reading output files in PowerShell needs `Get-Content -Encoding utf8`;
  plain `Get-Content` misdecodes `£`.

## CLI

Both OCR layers run standalone, taking a path argument or prompting:

```powershell
python paddleOcr.py invoice.png
python pdfOcr.py invoice.pdf
```
