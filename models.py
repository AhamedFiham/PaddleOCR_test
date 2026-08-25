"""SQLAlchemy models for confirmed invoice records."""

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import relationship

from database import Base


class Invoice(Base):
    """A human-verified invoice.

    Rows only ever land here after someone has confirmed the extracted
    fields, which is why ``status`` defaults to "verified" -- raw OCR
    output never reaches this table directly.
    """

    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_number = Column(String(100), index=True)
    invoice_date = Column(Date)
    vendor_name = Column(String(255), index=True)
    subtotal = Column(Numeric(14, 2))
    tax_amount = Column(Numeric(14, 2))
    total_amount = Column(Numeric(14, 2))
    currency = Column(String(3), default="GBP")
    file_path = Column(String(512))
    # Full OCR output kept verbatim so a bad read can be audited later.
    raw_ocr_json = Column(JSON)
    status = Column(String(32), nullable=False, default="verified")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    line_items = relationship(
        "InvoiceLineItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )


class InvoiceLineItem(Base):
    __tablename__ = "invoice_line_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description = Column(String(512))
    quantity = Column(Numeric(14, 3))
    unit_price = Column(Numeric(14, 2))
    line_total = Column(Numeric(14, 2))

    invoice = relationship("Invoice", back_populates="line_items")
