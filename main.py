"""Invoice OCR extraction service.

Stateless by design. An upload is OCR'd inside a temporary directory that
is deleted before the response is sent, and the extracted fields come
back as JSON. Nothing is persisted -- no database, no retained originals
-- so the service carries no volume, no migration and no backup, and a
redeploy is just a rebuild.

Callers that need to keep the original document should keep it on their
side; this service does not hand back a reference to one.

Run with:  uvicorn main:app --reload
Then drive the whole flow from http://127.0.0.1:8000/docs
"""

import os
import shutil
import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from extraction import extract_fields
from paddleOcr import get_ocr, run_ocr_on_image
from pdfOcr import run_ocr_on_pdf
from resize_utils import resize_for_ocr
from schemas import InvoiceExtractResponse

# .heic/.heif are what an iPhone hands over when the user picks an
# existing photo rather than taking one. resize_for_ocr converts them.
IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
    ".heic", ".heif", ".avif",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the OCR engine once, here, so the first upload does not pay
    # the model-load cost and no request ever constructs its own.
    get_ocr()
    yield


app = FastAPI(title="Invoice OCR Extraction", lifespan=lifespan)

# Browser callers are cross-origin: the front end is served from Vercel,
# the API from Railway. 5173 is Vite's default for local development.
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "https://x2-p-pwa.vercel.app",
]

# Vercel gives every preview deployment its own hostname
# (x2-p-pwa-git-<branch>-<team>.vercel.app), so testing a branch build
# would otherwise fail CORS even though production works.
VERCEL_PREVIEW_ORIGIN = r"https://x2-p-pwa-[a-z0-9-]+\.vercel\.app"

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
    allow_origin_regex=VERCEL_PREVIEW_ORIGIN,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _store_upload(upload: UploadFile, directory: str) -> str:
    """Write the upload into ``directory`` and return its path."""
    original = os.path.basename(upload.filename or "upload")
    extension = os.path.splitext(original)[1].lower()
    if extension != ".pdf" and extension not in IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{extension}'. Upload a PDF or an image.",
        )

    # The stored name is fixed rather than taken from the upload: the
    # directory is private to this one request, so there is nothing to
    # collide with, and no caller-supplied name reaches the filesystem.
    # Only the extension carries over, because both resize_for_ocr and
    # run_ocr_on_image branch on it.
    stored_path = os.path.join(directory, f"upload{extension}")
    with open(stored_path, "wb") as out:
        shutil.copyfileobj(upload.file, out)
    return stored_path


@app.get("/health")
def health():
    """Liveness probe. Cheap on purpose -- it must not touch the engine,
    so a host's health check keeps passing while a long OCR runs."""
    return {"status": "ok"}


# Sync def: FastAPI runs this in a worker thread, so the CPU-bound OCR
# call does not block the event loop.
@app.post("/invoices/extract", response_model=InvoiceExtractResponse)
def extract_invoice(file: UploadFile = File(...)):
    """OCR an uploaded invoice and return the extracted fields.

    The upload exists only for the life of the request. Every field may
    come back null -- an unreadable invoice is not an error -- so read
    ``field_confidence`` before trusting a value.
    """
    # resize_for_ocr writes its normalised copy alongside its input, so
    # scoping the request to one directory cleans up both files with it.
    workspace = tempfile.mkdtemp(prefix="invoice-ocr-")
    try:
        stored_path = _store_upload(file, workspace)

        if stored_path.lower().endswith(".pdf"):
            # pypdfium2 renders each page inside the pipeline, so there is
            # no image to resize here.
            lines = run_ocr_on_pdf(stored_path)
        else:
            # Uploads arrive straight from cameras and galleries, so this
            # runs unconditionally rather than trusting the source.
            lines = run_ocr_on_image(resize_for_ocr(stored_path))
    except HTTPException:
        # An unsupported file type is the caller's 400, not our 500.
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OCR failed: {exc}") from exc
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    return InvoiceExtractResponse(**extract_fields(lines), raw_ocr=lines)
