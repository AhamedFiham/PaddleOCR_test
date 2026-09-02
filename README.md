# Invoice OCR Extraction

Extracts structured data from invoice images and PDFs with PaddleOCR and
returns it as JSON, with a confidence score per field.

The service is **stateless**. There is no database and no stored upload:
each file is OCR'd inside a temporary directory that is deleted before
the response is sent. Keeping the original document, and keeping the
extracted record, are the caller's business.

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

Then open <http://127.0.0.1:8000/docs> and drive it from Swagger UI.

## API

### `POST /invoices/extract`

Multipart upload under the field name `file` — a PDF, or an image
(PNG/JPEG/BMP/TIFF/WebP/HEIC/AVIF). Returns the extracted fields.

```bash
curl -F "file=@invoice.pdf" https://your-api/invoices/extract
```

```json
{
  "invoice_number": "NW-88214",
  "invoice_date": "2026-06-09",
  "vendor_name": "Northwind Trading Ltd",
  "subtotal": 317.0,
  "tax_amount": 63.4,
  "total_amount": 380.4,
  "currency": "GBP",
  "location": "Chennai",
  "country": "India",
  "expense_type": "Courier/Shipping/Freight",
  "expense_code": "6-4100",
  "expense_category": "04. OFFICE EXPENSES",
  "expense_type_confidence": 0.95,
  "expense_type_alternatives": ["Postage", "Carriage Outwards"],
  "line_items": [
    {"description": "Steel brackets 40mm", "quantity": 12.0,
     "unit_price": 18.5, "line_total": 222.0}
  ],
  "field_confidence": {"invoice_number": 0.981, "total_amount": 1.0, "...": 0.0},
  "raw_ocr": [{"text": "Northwind Trading Ltd", "confidence": 0.997}]
}
```

Every field can be `null`. An invoice whose date is unreadable is still
worth returning, so a field the extractor could not fill is not an error
— which is why `field_confidence` matters more than the presence of a
value. Anything below ~0.7 is worth flagging in a review UI rather than
presenting as trustworthy.

`currency` is **not** defaulted. Guessing one nationality's currency on
an invoice from anywhere else is a wrong answer stated confidently, so an
undetected currency comes back `null` for the reviewer to fill in.

`location` is the place as printed — "Chennai", not "India". `country`
is the wider region, and is also what resolves a currency symbol several
countries share: `Rs` is LKR beside a Colombo address and INR beside a
Chennai one, and `$` is AUD in Sydney.

`expense_type` matches the front end's dropdown labels exactly, so it can
be pre-selected. Treat it as a suggestion, never an answer: roughly a
third of those labels differ only by business context that is not on the
receipt — the same restaurant bill is "Entertainment - Client" or
"Entertainment - Staff" depending only on who was there. Those score low
and return their siblings in `expense_type_alternatives`, so the claimant
picks from two or three rather than a list of seventy-three.

`raw_ocr` is the full reading-order OCR output, returned so a bad
extraction can be diagnosed without re-uploading the file.

Errors: `400` for an unsupported file type, `500` if OCR itself fails.

### `GET /health`

Liveness probe for a load balancer or platform health check. Deliberately
cheap — it does not touch the OCR engine, so it keeps answering while a
long extract is running.

## Deploying to a server

The front end is deployed separately, so it calls this API cross-origin.

```bash
cp .env.example .env      # then set CORS_ORIGINS
docker compose up -d --build
```

Two containers: the API, and an nginx reverse proxy on port 80. No
database, no volumes — the service keeps nothing between requests, so a
redeploy is only a rebuild and there is nothing to migrate or back up.

`CORS_ORIGINS` is the one setting that must be right, and the stack
refuses to start without it: the **browser origin the front end is served
from**, e.g. `https://invoices.example.com`. Not the API's own URL.
Scheme, host and port must match exactly, with no trailing slash.

Because there is no state, this also deploys as a bare container to any
platform that can run one (Railway, Render, Cloud Run, Fly). The
Dockerfile honours the injected `$PORT`. Size the instance for **~2GB RAM
and 2 vCPU**: the models sit resident in memory, and the container is
CPU-bound while extracting.

### What the front-end developer needs

- **The API base URL**, e.g. `https://invoice-api.example.com`. The only
  endpoint they need is `POST /invoices/extract`.
- **Their origin added to `CORS_ORIGINS`** before anything works from a
  browser. A missing origin fails as an opaque CORS error, not a 4xx.
- **HTTPS on this API if their site is HTTPS.** Browsers block an HTTPS
  page from calling an HTTP endpoint, and no CORS setting overrides that.
  Terminate TLS at the proxy, or put this behind a load balancer that
  does, before integration.
- **A raised request timeout.** Extraction is CPU-bound and synchronous;
  a phone photo runs a few seconds on the default `tiny` models, but a
  long multi-page PDF scales per page. Measure with your own documents,
  then set the client timeout well above the worst case — nginx here is
  already at 600s. This is the most likely integration failure.
- **Requests are serialised.** One worker, one extract at a time (see
  `_PREDICT_LOCK` in [paddleOcr.py](paddleOcr.py)), so concurrent uploads
  queue rather than run in parallel.

## Notes

- **oneDNN must stay disabled.** `enable_mkldnn=False` in
  [paddleOcr.py](paddleOcr.py) is required, not a tuning choice: with it
  enabled, paddlepaddle 3.3.1 aborts text detection on this platform with
  `ConvertPirAttribute2RuntimeAttribute not support
  [pir::ArrayAttribute<pir::DoubleAttribute>]`.
- **`OCR_MODEL_SIZE` is the main speed/accuracy lever** (`tiny` |`small` |
  `medium`, default `tiny`; measured timings are in
  [paddleOcr.py](paddleOcr.py)). The Dockerfile bakes the matching model
  weights into the image, so changing it needs a rebuild — otherwise the
  runtime asks for weights the image does not have and downloads them on
  the first request.
- **The OCR engine is built once**, at app startup, and shared. Never
  construct `PaddleOCR(...)` inside a request handler.
- Reading order is reconstructed from detection geometry, because raw
  PaddleOCR order interleaves columns and separates labels from values.
- Detections below 0.5 confidence are dropped as noise.
- Reading output files in PowerShell needs `Get-Content -Encoding utf8`;
  plain `Get-Content` misdecodes `£`.

## Licensing

Everything here is free of charge and self-hosted: no API key, no
per-request cost, and no document leaves your server.

| Component | Licence |
| --- | --- |
| paddleocr, paddlepaddle, paddlex, PP-OCRv6 models | Apache 2.0 |
| pypdfium2 / PDFium | BSD-3-Clause + Apache 2.0 |
| pillow, fastapi, uvicorn | Permissive (MIT/BSD-style) |
| **pi-heif** (HEIC decoding) | **LGPLv3** |

Everything is permissive except the HEIC decoder, which is LGPLv3 —
linkable from commercial software without any obligation on the
surrounding source, since it loads as a shared library.

Note for anyone tempted to swap it: **use `pi-heif`, not
`pillow-heif`.** They share an author and decode identically, but
pillow-heif's wheels bundle the x265 *encoder* and are therefore GPLv2.
This service only ever decodes, so that would mean carrying a copyleft
obligation for code that never runs — and it would attach the moment the
service is shipped to a client as a container rather than hosted for
them.

## CLI

Both OCR layers run standalone, taking a path argument or prompting:

```powershell
python paddleOcr.py invoice.png
python pdfOcr.py invoice.pdf
```
