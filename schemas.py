"""Pydantic request/response models for the two-phase invoice flow."""

from datetime import date, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class OcrLine(BaseModel):
    text: str
    confidence: float


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    line_total: Optional[float] = None


class LineItemOut(LineItem):
    model_config = ConfigDict(from_attributes=True)

    id: int


class InvoiceFields(BaseModel):
    """The fields the extractor produces and the reviewer may edit."""

    invoice_number: Optional[str] = None
    invoice_date: Optional[date] = None
    vendor_name: Optional[str] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    currency: Optional[str] = "GBP"
    line_items: List[LineItem] = Field(default_factory=list)


class InvoiceExtractResponse(InvoiceFields):
    """Draft returned by /invoices/extract. Nothing is persisted yet."""

    field_confidence: Dict[str, float] = Field(default_factory=dict)
    file_path: str
    raw_ocr: List[OcrLine] = Field(default_factory=list)


class InvoiceConfirm(InvoiceFields):
    """Human-confirmed payload accepted by POST /invoices."""

    file_path: Optional[str] = None
    raw_ocr: List[OcrLine] = Field(default_factory=list)


class InvoiceOut(InvoiceFields):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_path: Optional[str] = None
    status: str
    created_at: datetime
    line_items: List[LineItemOut] = Field(default_factory=list)
