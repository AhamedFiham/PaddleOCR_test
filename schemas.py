"""Pydantic response models for the extraction endpoint."""

from datetime import date
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class OcrLine(BaseModel):
    text: str
    confidence: float


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None


class InvoiceExtractResponse(BaseModel):
    """Everything the service knows about one uploaded document.

    Every field is optional because a value the extractor could not read
    comes back as null rather than failing the request -- an invoice with
    an unreadable date is still worth returning. ``field_confidence``
    scores each field 0-1 and is what tells the caller how much to trust
    the values that did come back; anything below ~0.7 is worth flagging
    for review rather than presenting as fact.

    ``raw_ocr`` is the full reading-order OCR output, kept in the response
    so a bad extraction can be diagnosed without re-uploading the file.
    """

    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    vendor_name: Optional[str] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    # No default currency: guessing one nationality's currency on an
    # invoice from anywhere else is worse than returning null and letting
    # the reviewer fill it in.
    currency: Optional[str] = None
    # The place as printed on the invoice, e.g. "Chennai" -- what an
    # expense record wants. `country` is the wider region it sits in, and
    # is also what resolves a shared currency symbol like "$" or "Rs".
    location: Optional[str] = None
    country: Optional[str] = None

    # Suggested expense category. `expense_type` matches the front end's
    # dropdown labels exactly. It is a suggestion to pre-select, never an
    # answer to submit unreviewed: many of the leaves differ only by
    # business context that is not on the receipt, which is what
    # `expense_type_alternatives` is for -- the other plausible choices,
    # to offer first instead of a list of seventy.
    expense_type: Optional[str] = None
    expense_code: Optional[str] = None
    expense_category: Optional[str] = None
    expense_type_confidence: float = 0.0
    expense_type_alternatives: List[str] = Field(default_factory=list)
    line_items: List[LineItem] = Field(default_factory=list)
    field_confidence: Dict[str, float] = Field(default_factory=dict)
    raw_ocr: List[OcrLine] = Field(default_factory=list)
