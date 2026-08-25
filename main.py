"""Invoice OCR service.

Two-phase by design: /invoices/extract only ever returns a draft, and
/invoices writes to MySQL. Raw OCR output is never persisted as a record
without a human confirming it first.

Run with:  uvicorn main:app --reload
Then drive the whole flow from http://127.0.0.1:8000/docs
"""

import os
import shutil
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, selectinload

import models
from database import Base, engine, get_db
from extraction import extract_fields
from paddleOcr import get_ocr, run_ocr_on_image
from pdfOcr import run_ocr_on_pdf
from resize_utils import resize_for_ocr
from schemas import InvoiceConfirm, InvoiceExtractResponse, InvoiceOut

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # The extract phase needs no database, so a missing MySQL must not
    # stop the service booting -- only the write endpoints should fail.
    try:
        Base.metadata.create_all(bind=engine)
        app.state.db_ready = True
    except Exception as exc:
        app.state.db_ready = False
        print(f"[startup] database unavailable, /invoices writes disabled: {exc}")

    # Build the OCR engine once, here, so the first upload does not pay
    # the model-load cost and no request ever constructs its own.
    get_ocr()
    yield


app = FastAPI(title="Invoice OCR Extraction", lifespan=lifespan)

# Browser callers are cross-origin during development: the React dev
# server runs on its own port. 5173 is Vite's default, 3000 Create React
# App's. Add the deployed front-end URL here when there is one.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
]

# Once the front end is served from somewhere else -- another machine on
# the LAN, a phone, a deployed URL -- add that origin too, without having
# to edit this file:
#     $env:CORS_ORIGINS = "http://192.168.1.42:5173,https://app.example.com"
# An origin missing from this list has its requests blocked by the
# browser. http://localhost and http://127.0.0.1 count as different
# origins, as does every distinct port.
ALLOWED_ORIGINS += [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _save_upload(upload: UploadFile) -> str:
    """Store the upload under a collision-proof name, return its path."""
    original = os.path.basename(upload.filename or "upload")
    extension = os.path.splitext(original)[1].lower()
    if extension != ".pdf" and extension not in IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{extension}'. Upload a PDF or an image.",
        )

    stored_name = f"{uuid.uuid4().hex}_{original}"
    stored_path = os.path.join(UPLOAD_DIR, stored_name)
    with open(stored_path, "wb") as out:
        shutil.copyfileobj(upload.file, out)
    return stored_path


# Sync def: FastAPI runs this in a worker thread, so the CPU-bound OCR
# call does not block the event loop.
@app.post("/invoices/extract", response_model=InvoiceExtractResponse)
def extract_invoice(file: UploadFile = File(...)):
    """OCR an uploaded invoice and return a draft. Nothing is saved to
    the database at this step -- the response is for human review."""
    stored_path = _save_upload(file)

    try:
        if stored_path.lower().endswith(".pdf"):
            # pypdfium2 renders each page inside the pipeline, so there is
            # no image to resize here.
            lines = run_ocr_on_pdf(stored_path)
        else:
            # Uploads arrive straight from cameras and galleries, so this
            # runs unconditionally rather than trusting the source.
            ocr_path = resize_for_ocr(stored_path)
            lines = run_ocr_on_image(ocr_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OCR failed: {exc}") from exc

    fields = extract_fields(lines)
    return InvoiceExtractResponse(
        **fields,
        file_path=stored_path,
        raw_ocr=lines,
    )


@app.post("/invoices", response_model=InvoiceOut, status_code=201)
def create_invoice(payload: InvoiceConfirm, db: Session = Depends(get_db)):
    """Persist a confirmed invoice, with its line items, to MySQL."""
    invoice = models.Invoice(
        invoice_number=payload.invoice_number,
        invoice_date=payload.invoice_date,
        vendor_name=payload.vendor_name,
        subtotal=payload.subtotal,
        tax_amount=payload.tax_amount,
        total_amount=payload.total_amount,
        currency=payload.currency,
        file_path=payload.file_path,
        raw_ocr_json=[line.model_dump() for line in payload.raw_ocr],
        status="verified",
    )
    invoice.line_items = [
        models.InvoiceLineItem(
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            line_total=item.line_total,
        )
        for item in payload.line_items
    ]

    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


@app.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = (
        db.query(models.Invoice)
        .options(selectinload(models.Invoice.line_items))
        .filter(models.Invoice.id == invoice_id)
        .first()
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice
