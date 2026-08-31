"""Structured field extraction from reading-order OCR lines.

Strategy is deliberately generic first: find a label ("Invoice No",
"Amount Due", "VAT") and capture the value that follows it, either on the
same line or in the next few lines. Per-vendor overrides layer on top only
where the generic patterns are shown to miss -- see VENDOR_OVERRIDES.

Every field comes back with a confidence score in ``field_confidence`` so
the review UI can flag weak reads instead of silently trusting them.
"""

import os
import re
from datetime import date

# Your own company name(s), comma-separated, e.g. "Hela Brands".
# Every purchase invoice carries the buyer's name as well as the
# supplier's, and on some layouts the bill-to block sits above the
# supplier's own letterhead -- so without this the buyer gets extracted
# as the vendor. Set OWN_COMPANY_NAMES to exclude it.
OWN_COMPANY_NAMES = [
    name.strip().lower()
    for name in os.getenv("OWN_COMPANY_NAMES", "").split(",")
    if name.strip()
]

# --- confidence weights -------------------------------------------------
# How much to trust a value based on how specific the label that found it
# was. A value sitting next to "Invoice Number:" is worth more than one
# picked up by a bare "Total".
ANCHOR_STRONG = 1.0
ANCHOR_WEAK = 0.75
INFERRED = 0.5

# How far past an anchor to look when the value is not on the same line.
LOOKAHEAD = 3

MONEY_TOKEN = r"[-(]?\s*(?:[£$€]\s*)?\d[\d,]*(?:\.\d{1,2})?\s*\)?"
MONEY_RE = re.compile(MONEY_TOKEN)
# A cell that is *only* a number. Distinguishing this from "contains a
# number" matters for table rows: "Steel brackets 40mm" is a description,
# not a quantity, even though a money pattern matches inside it.
IS_PURE_MONEY = re.compile(rf"^\s*{MONEY_TOKEN}\s*(?:CR)?\s*$", re.I)
# --- currency and country ----------------------------------------------
# ISO 4217 codes recognised when written out. An explicit code is the
# strongest signal there is: it needs no disambiguation.
CURRENCY_CODES = (
    "GBP", "USD", "EUR", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF",
    "RON", "BGN", "TRY", "RUB", "UAH", "ILS", "AED", "SAR", "QAR", "KWD",
    "BHD", "OMR", "EGP", "ZAR", "NGN", "KES", "GHS", "MAD", "TND",
    "INR", "LKR", "PKR", "BDT", "NPR", "MVR",
    "CNY", "RMB", "JPY", "KRW", "HKD", "TWD", "SGD", "MYR", "THB", "IDR",
    "PHP", "VND", "AUD", "NZD", "CAD", "MXN", "BRL", "ARS", "CLP", "COP",
)

# Symbols that map to exactly one currency.
CURRENCY_SYMBOLS = {
    "£": "GBP", "€": "EUR", "₹": "INR", "₩": "KRW", "₪": "ILS",
    "₺": "TRY", "₦": "NGN", "₱": "PHP", "฿": "THB", "₫": "VND",
    "₽": "RUB", "₴": "UAH", "﷼": "SAR", "₸": "KZT", "₮": "MNT",
}

# Prefixed dollar and rupee forms. Checked before the bare symbols below,
# because "A$" would otherwise match as a plain "$".
QUALIFIED_SYMBOLS = {
    "US$": "USD", "A$": "AUD", "AU$": "AUD", "C$": "CAD", "CA$": "CAD",
    "NZ$": "NZD", "S$": "SGD", "HK$": "HKD", "NT$": "TWD", "R$": "BRL",
    "RM": "MYR", "Rp": "IDR", "CHF": "CHF", "Kč": "CZK", "zł": "PLN",
}

# These must attach to an actual figure, and must not start mid-word:
# a plain substring test reads the "RM" of "Ninja RMM" as Malaysian
# ringgit and reports a Huddersfield invoice as MYR.
QUALIFIED_SYMBOL_RE = {
    re.compile(rf"(?<![A-Za-z]){re.escape(symbol)}\s*[\d(]"): code
    for symbol, code in QUALIFIED_SYMBOLS.items()
}

# Symbols several countries share. These cannot be resolved on their own,
# only by working out which country the invoice comes from -- which is
# why location detection and currency detection are the same problem.
AMBIGUOUS_SYMBOLS = {
    "$": ("USD", "AUD", "CAD", "SGD", "HKD", "NZD", "MXN"),
    "¥": ("JPY", "CNY"),
    "₨": ("LKR", "PKR", "NPR"),
    "Rs": ("INR", "LKR", "PKR", "NPR"),
    "R": ("ZAR",),
}

# The symbol must not begin mid-token: without the lookbehind, the "R"
# in a bill number like "SR2" reads as a South African rand and a Chennai
# receipt comes back as ZAR.
AMBIGUOUS_SYMBOL_RE = {
    symbol: re.compile(rf"(?<![A-Za-z0-9]){re.escape(symbol)}\s*[\d(]")
    for symbol in AMBIGUOUS_SYMBOLS
}

# Tax registration formats that identify a country outright. CGST, SGST
# and IGST exist only under India's GST regime, and GSTIN is its
# registration number -- far stronger evidence than a city name.
TAX_ID_HINTS = {
    r"\bGSTIN\b|\b[CSI]GST\b": "India",
    r"\bABN\b|\bACN\b": "Australia",
    r"\bBTW\b": "Netherlands",
    r"\bUID\b|\bMWST\b": "Switzerland",
}

# Currency written as a word.
CURRENCY_WORDS = {
    r"pounds?\s*sterling|sterling": "GBP", r"\beuros?\b": "EUR",
    r"\byen\b": "JPY", r"\byuan\b|renminbi": "CNY", r"\bwon\b": "KRW",
    r"\bbaht\b": "THB", r"\bringgit\b": "MYR", r"\brand\b": "ZAR",
    r"\bnaira\b": "NGN", r"\bdirhams?\b": "AED", r"\briyals?\b": "SAR",
    r"\btaka\b": "BDT", r"\brupiah\b": "IDR",
}

# Country -> (currency, canonical name). Drives both the location field
# and the disambiguation above.
COUNTRIES = {
    r"sri\s*lanka": ("LKR", "Sri Lanka"),
    r"united\s*kingdom|\bu\.?k\.?\b|england|scotland|wales": ("GBP", "United Kingdom"),
    r"\bindia\b": ("INR", "India"),
    r"pakistan": ("PKR", "Pakistan"),
    r"bangladesh": ("BDT", "Bangladesh"),
    r"\bnepal\b": ("NPR", "Nepal"),
    r"united\s*states|\bu\.?s\.?a\.?\b": ("USD", "United States"),
    r"australia": ("AUD", "Australia"),
    r"new\s*zealand": ("NZD", "New Zealand"),
    r"\bcanada\b": ("CAD", "Canada"),
    r"singapore": ("SGD", "Singapore"),
    r"hong\s*kong": ("HKD", "Hong Kong"),
    r"malaysia": ("MYR", "Malaysia"),
    r"indonesia": ("IDR", "Indonesia"),
    r"thailand": ("THB", "Thailand"),
    r"vietnam|viet\s*nam": ("VND", "Vietnam"),
    r"philippines": ("PHP", "Philippines"),
    r"\bchina\b": ("CNY", "China"),
    r"\bjapan\b": ("JPY", "Japan"),
    r"south\s*korea|\bkorea\b": ("KRW", "South Korea"),
    r"germany|deutschland": ("EUR", "Germany"),
    r"france": ("EUR", "France"),
    r"netherlands|holland": ("EUR", "Netherlands"),
    r"\bspain\b": ("EUR", "Spain"),
    r"\bitaly\b": ("EUR", "Italy"),
    r"ireland": ("EUR", "Ireland"),
    r"switzerland": ("CHF", "Switzerland"),
    r"sweden": ("SEK", "Sweden"),
    r"norway": ("NOK", "Norway"),
    r"denmark": ("DKK", "Denmark"),
    r"poland": ("PLN", "Poland"),
    r"turkey|t.rkiye": ("TRY", "Turkey"),
    r"south\s*africa": ("ZAR", "South Africa"),
    r"\bkenya\b": ("KES", "Kenya"),
    r"nigeria": ("NGN", "Nigeria"),
    r"\begypt\b": ("EGP", "Egypt"),
    r"\buae\b|united\s*arab|\bdubai\b|abu\s*dhabi": ("AED", "United Arab Emirates"),
    r"saudi": ("SAR", "Saudi Arabia"),
    r"\bqatar\b": ("QAR", "Qatar"),
    r"\bbrazil\b": ("BRL", "Brazil"),
    r"\bmexico\b": ("MXN", "Mexico"),
}

# Cities, with the country each belongs to. These serve two purposes:
# they are the location worth reporting, and they pin down a country when
# the invoice never names one -- which domestic invoices rarely do.
CITY_COUNTRY = {
    # Sri Lanka
    "Colombo": "Sri Lanka", "Panadura": "Sri Lanka", "Moratuwa": "Sri Lanka",
    "Negombo": "Sri Lanka", "Kandy": "Sri Lanka", "Katunayake": "Sri Lanka",
    "Gampaha": "Sri Lanka", "Biyagama": "Sri Lanka", "Dehiwala": "Sri Lanka",
    "Boralesgamuwa": "Sri Lanka", "Ratmalana": "Sri Lanka", "Kelaniya": "Sri Lanka",
    # United Kingdom
    "London": "United Kingdom", "Manchester": "United Kingdom",
    "Birmingham": "United Kingdom", "Leeds": "United Kingdom",
    "Glasgow": "United Kingdom", "Bristol": "United Kingdom",
    "Huddersfield": "United Kingdom", "Chelmsford": "United Kingdom",
    "Biggleswade": "United Kingdom", "Edinburgh": "United Kingdom",
    "Liverpool": "United Kingdom", "Sheffield": "United Kingdom",
    "Nottingham": "United Kingdom", "Cardiff": "United Kingdom",
    "Belfast": "United Kingdom", "Gretna": "United Kingdom",
    # India
    "Chennai": "India", "Mumbai": "India", "Bengaluru": "India",
    "Bangalore": "India", "Delhi": "India", "Hyderabad": "India",
    "Kolkata": "India", "Pune": "India", "Tirupur": "India",
    "Coimbatore": "India", "Ahmedabad": "India", "Noida": "India",
    "Gurgaon": "India", "Jaipur": "India",
    # Rest of Asia
    "Dhaka": "Bangladesh", "Chittagong": "Bangladesh",
    "Karachi": "Pakistan", "Lahore": "Pakistan", "Faisalabad": "Pakistan",
    "Kathmandu": "Nepal",
    "Dubai": "United Arab Emirates", "Sharjah": "United Arab Emirates",
    "Abu Dhabi": "United Arab Emirates",
    "Singapore": "Singapore", "Kuala Lumpur": "Malaysia", "Penang": "Malaysia",
    "Bangkok": "Thailand", "Jakarta": "Indonesia", "Manila": "Philippines",
    "Hanoi": "Vietnam", "Shanghai": "China", "Shenzhen": "China",
    "Guangzhou": "China", "Beijing": "China", "Tokyo": "Japan",
    "Osaka": "Japan", "Seoul": "South Korea",
    # Elsewhere
    "Sydney": "Australia", "Melbourne": "Australia", "Brisbane": "Australia",
    "Perth": "Australia", "Auckland": "New Zealand",
    "New York": "United States", "Los Angeles": "United States",
    "Chicago": "United States", "Toronto": "Canada", "Vancouver": "Canada",
    "Berlin": "Germany", "Hamburg": "Germany", "Munich": "Germany",
    "Paris": "France", "Amsterdam": "Netherlands", "Madrid": "Spain",
    "Milan": "Italy", "Dublin": "Ireland", "Zurich": "Switzerland",
    "Geneva": "Switzerland", "Stockholm": "Sweden", "Copenhagen": "Denmark",
    "Warsaw": "Poland", "Istanbul": "Turkey", "Cairo": "Egypt",
    "Nairobi": "Kenya", "Lagos": "Nigeria", "Johannesburg": "South Africa",
    "Cape Town": "South Africa",
}

CITY_PATTERN = re.compile(
    r"\b(" + "|".join(sorted((re.escape(c) for c in CITY_COUNTRY), key=len, reverse=True)) + r")\b",
    re.I,
)

# Words that mark a line as part of a street address. The line after one
# of these is usually the town, which is how an unlisted city is found.
ADDRESS_MARKER = re.compile(
    r"\b(road|rd|street|st|avenue|ave|lane|ln|drive|dr|way|place|court|"
    r"nagar|mawatha|building|floor|block|plaza|estate|park|zone|"
    r"industrial|no\.?\s*\d|\d+/\d+)\b",
    re.I,
)

# Never a town, however address-like the surrounding lines look.
NOT_A_PLACE = re.compile(
    r"\d|@|www\.|http|invoice|total|tel|phone|fax|email|vat|gst|reg|"
    r"\bltd\b|\blimited\b|\bplc\b|\bpvt\b|\binc\b|"
    r"\bpage|\bnumber\b|\bdate\b|\bref\b|\bpayment\b|\baccount\b|\bcustomer\b|"
    # Business headings that sit in the same position as a town and are
    # otherwise shaped exactly like one -- Biffa's "Credit Control".
    r"\bcontrol\b|\bservices?\b|\bdepartment\b|\bdept\b|\bremittance\b|"
    r"\benquir|\bdeliver|\bdescription\b|\bterms\b|\bbank\b|\bsort\b|"
    r"\bswift\b|\biban\b|\bcharges?\b|\bbalance\b|\bdue\b|\bcompany\b|"
    r"\baddress\b|\bcontact\b|\bsupplier\b|\bbranch\b|\bdescription\b",
    re.I,
)

# A town is letters, spaces and the odd hyphen or apostrophe -- never a
# colon, digit or slash. Without this, DHL's "Number Of Pages:" line was
# accepted as a location.
PLACE_SHAPE = re.compile(r"^[A-Za-z][A-Za-z\s\-'\.]{2,29}$")

# A summary figure written with its currency, e.g. "EUR (1,0841) 476,14".
CURRENCY_LINE = re.compile(rf"^(?:{'|'.join(CURRENCY_CODES)})\b.*\d", re.I)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Numeric dates here are day-first (UK vendors). 03/04/2026 is 3 April.
DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), ("y", "m", "d")),
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b"), ("d", "m", "y")),
    (
        re.compile(
            r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{2,4})\b"
        ),
        ("d", "mon", "y"),
    ),
    (
        re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{2,4})\b"),
        ("mon", "d", "y"),
    ),
]

# Labels that contain a money word but never introduce a money value.
# The last alternative catches a bare "VAT: 740 1819 52" -- a UK VAT
# registration number with no "Reg" label, which otherwise reads as an
# amount of 52.00.
NOT_AN_AMOUNT = re.compile(
    r"vat\s*(reg|registration|no\b|number|rate)"
    r"|company\s*(reg|no)"
    r"|tax\s*(point|rate|code)"
    r"|\bvat\b\W{0,4}\d{3}\s*\d{4}\s*\d{2}\b",
    re.I,
)

INVOICE_NUMBER_ANCHORS = [
    (re.compile(r"invoice\s*(?:no|number|num|#)\s*[.:#]?", re.I), ANCHOR_STRONG),
    # "Inv No." / "Inv #" -- abbreviated forms are common on real invoices.
    (re.compile(r"\binv\.?\s*(?:no|number|num|#)\s*[.:#]?", re.I), ANCHOR_STRONG),
    (re.compile(r"(?:document|doc)\s*(?:no|number|#)\s*[.:#]?", re.I), ANCHOR_STRONG),
    (re.compile(r"credit\s*note\s*(?:no|number|#)\s*[.:#]?", re.I), ANCHOR_STRONG),
    # Utility and telecoms bills label it "Bill reference", not "Invoice".
    (re.compile(r"bill\s*(?:reference|ref|number|no)\s*[.:#]?", re.I), ANCHOR_STRONG),
    (re.compile(r"\bour\s*ref(?:erence)?\s*[.:#]?", re.I), ANCHOR_WEAK),
    # "Invoice:INV01664685" -- a colon straight after the word is far more
    # specific than a bare "INVOICE" heading, so it outranks it.
    (re.compile(r"\binvoice\s*[:#]\s*", re.I), ANCHOR_WEAK + 0.1),
    (re.compile(r"\binvoice\b\s*[.:#]?", re.I), ANCHOR_WEAK),
]

DATE_ANCHORS = [
    (re.compile(r"invoice\s*date\s*[.:]?", re.I), ANCHOR_STRONG),
    (re.compile(r"(?:tax\s*point|issue\s*date|date\s*of\s*issue)\s*[.:]?", re.I), ANCHOR_STRONG),
    # "Dated 25/06/2026" -- \bdate\b does not match "Dated".
    (re.compile(r"\bdated\b\s*[.:]?", re.I), ANCHOR_STRONG),
    (re.compile(r"\bdate\b\s*[.:]?", re.I), ANCHOR_WEAK),
]

# Ordered by how unambiguously each label means "the final payable figure".
TOTAL_ANCHORS = [
    (re.compile(r"(?:amount|balance)\s*due\s*[.:]?", re.I), ANCHOR_STRONG),
    (re.compile(r"total\s*due\s*[.:]?", re.I), ANCHOR_STRONG),
    (re.compile(r"(?:grand|invoice)\s*total\s*[.:]?", re.I), ANCHOR_STRONG),
    (re.compile(r"total\s*(?:inc|incl|including)\.?\s*(?:vat|tax)\s*[.:]?", re.I), ANCHOR_STRONG),
    (re.compile(r"total\s*gbp\s*[.:]?", re.I), ANCHOR_WEAK),
    (re.compile(r"\btotal\b\s*[.:]?", re.I), ANCHOR_WEAK),
]

SUBTOTAL_ANCHORS = [
    (re.compile(r"sub[\s-]*total\s*[.:]?", re.I), ANCHOR_STRONG),
    (re.compile(r"(?:total\s*net|net\s*total|net\s*amount)\s*[.:]?", re.I), ANCHOR_STRONG),
    (re.compile(r"total\s*(?:exc|excl|excluding)\.?\s*(?:vat|tax)\s*[.:]?", re.I), ANCHOR_STRONG),
    (re.compile(r"goods\s*(?:total|value)\s*[.:]?", re.I), ANCHOR_WEAK),
    (re.compile(r"\bnet\b\s*[.:]?", re.I), ANCHOR_WEAK),
]

TAX_ANCHORS = [
    (re.compile(r"(?:total\s*)?vat\s*(?:amount|total|charged)\s*[.:]?", re.I), ANCHOR_STRONG),
    (re.compile(r"(?:sales\s*)?tax\s*(?:amount|total)\s*[.:]?", re.I), ANCHOR_STRONG),
    (re.compile(r"\bvat\b\s*(?:@\s*\d+(?:\.\d+)?\s*%)?\s*[.:]?", re.I), ANCHOR_WEAK),
    (re.compile(r"\btax\b\s*[.:]?", re.I), ANCHOR_WEAK),
]

# An invoice number is alphanumeric, at least 4 chars, and contains a digit.
INVOICE_NUMBER_RE = re.compile(r"\b(?=[A-Z0-9/\-]{4,30}\b)(?=[A-Z0-9/\-]*\d)[A-Z0-9][A-Z0-9/\-]{3,29}\b")

COMPANY_SUFFIX = re.compile(
    r"\b(ltd|limited|plc|llp|inc|incorporated|corp|corporation|gmbh|bv|sa|group|services|solutions)\b\.?",
    re.I,
)

# Noise that shows up at the top of a page but is never the vendor name.
VENDOR_NOISE = re.compile(
    r"^(invoice|tax\s*invoice|credit\s*note|statement|page\s*\d|vat|date|to|bill\s*to|invoice\s*to)\b"
    r"|quer(y|ies)|enquir|helpline|customer\s*service|contact\s*us"
    # Document-type headings, wherever they appear on the line.
    r"|sales\s*invoice|tax\s*invoice|purchase\s*order|remittance|depot",
    re.I,
)

# Prose, not a company name. "The services detailed below are now due for
# payment." otherwise reads as a vendor because it sits high on the page
# and contains no digits. A real company name carries none of these.
VENDOR_PROSE = re.compile(
    r"\b(the|are|is|was|were|please|below|now|your|this|our|will|has|have|detailed|due)\b",
    re.I,
)

ITEM_HEADER = re.compile(r"\b(description|item|details|particulars|product)\b", re.I)
# The remaining cells of a table header row. They arrive as separate
# detections ("QTY", "Description", "Unit Price", "Total"), so the header
# has to be consumed as a whole -- otherwise its own "Total" cell trips
# the ITEM_STOP check before the first real row is reached.
HEADER_CELL = re.compile(
    r"^(qty|quantity|description|details|item(?:\s*no)?|particulars|product|"
    r"unit\s*price|price|rate|amount|total|line\s*total|net|value|vat|tax|"
    r"discount|code|units?)\s*[.:]?$",
    re.I,
)
ITEM_STOP = re.compile(
    r"\b(sub[\s-]*total|total|vat|tax|amount\s*due|balance|payment|terms|remittance)\b",
    re.I,
)

# Vendors whose layout defeats the generic patterns above. Deliberately
# empty: populate only from observed failures against real invoices, never
# speculatively. Shape:
#   "biffa": {"invoice_number": [re.compile(...)], ...}
VENDOR_OVERRIDES = {}


# --- value parsers ------------------------------------------------------

def parse_money(text):
    """Parse a money token into a float, or None.

    Handles thousands separators, currency symbols, parenthesised
    negatives and a trailing CR credit marker.
    """
    if text is None:
        return None
    raw = text.strip()
    if not raw:
        return None
    negative = raw.startswith("(") and raw.rstrip().endswith(")")
    if re.search(r"\bCR\b", raw, re.I):
        negative = True
    cleaned = re.sub(r"[^\d.,\-]", "", raw)
    if cleaned in ("", "-", ".", "-."):
        return None

    # Work out which separator is the decimal point. European invoices
    # write 476,14 for 476.14 and 1.234,56 for 1234.56, so assuming the
    # comma is always a thousands separator turns 476,14 into 47614.
    last_dot = cleaned.rfind(".")
    last_comma = cleaned.rfind(",")
    if last_dot >= 0 and last_comma >= 0:
        decimal_sep = "." if last_dot > last_comma else ","
    elif last_comma >= 0:
        # A lone comma is decimal only when exactly two digits follow it.
        decimal_sep = "," if re.search(r",\d{2}$", cleaned) else ""
    elif last_dot >= 0:
        decimal_sep = "." if re.search(r"\.\d{1,2}$", cleaned) else ""
    else:
        decimal_sep = ""

    if decimal_sep:
        whole, _, fraction = cleaned.rpartition(decimal_sep)
        cleaned = re.sub(r"[.,]", "", whole) + "." + fraction
    else:
        cleaned = re.sub(r"[.,]", "", cleaned)

    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -abs(value) if negative else value


def _looks_like_identifier(token):
    """True for long digit runs that are references, not amounts.

    Account, phone and registration numbers otherwise parse cleanly as
    money -- DHL yields a "VAT" of 8144626932 without this. Real amounts
    that large carry a decimal part or thousands separators.
    """
    digits = re.sub(r"\D", "", token)
    return len(digits) >= 7 and "." not in token and "," not in token


def find_money(text):
    """Return the last money-looking value in a string, or None.

    Last rather than first: on a line like "VAT @ 20% 1,234.56" the rate
    comes before the amount. Percentages and identifiers are skipped, so
    "TOTAL VAT 20%" yields nothing rather than an amount of 20.00.
    """
    text = text or ""
    candidates = []
    for match in MONEY_RE.finditer(text):
        token = match.group(0)
        # A figure followed by "%" is a rate, never an amount.
        if re.match(r"\s*%", text[match.end():]):
            continue
        if _looks_like_identifier(token):
            continue
        value = parse_money(token)
        if value is not None:
            candidates.append(value)
    return candidates[-1] if candidates else None


def _normalise_year(value):
    year = int(value)
    if year < 100:
        year += 2000 if year < 70 else 1900
    return year


def parse_date(text):
    """Parse the first date found in a string into a ``datetime.date``."""
    if not text:
        return None
    for pattern, order in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        parts = dict(zip(order, match.groups()))
        try:
            if "mon" in parts:
                month = MONTHS.get(parts["mon"][:4].lower().rstrip("."))
                if month is None:
                    month = MONTHS.get(parts["mon"][:3].lower())
                if month is None:
                    continue
            else:
                month = int(parts["m"])
            day = int(parts["d"])
            year = _normalise_year(parts["y"])
            # Day-first misread: 25/07 is fine, but 07/25 must be swapped.
            if month > 12 and day <= 12:
                day, month = month, day
            return date(year, month, day)
        except (ValueError, KeyError):
            continue
    return None


def parse_invoice_number(text):
    """Pull an invoice-number-shaped token out of a string."""
    if not text:
        return None
    for match in INVOICE_NUMBER_RE.finditer(text.upper()):
        token = match.group(0).strip("-/")
        # Reject things that are really dates or bare years.
        if re.fullmatch(r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}", token):
            continue
        if re.fullmatch(r"(19|20)\d{2}", token):
            continue
        if len(token) >= 4:
            return token
    return None


# --- anchor search ------------------------------------------------------

def _search_anchored(lines, anchors, parser, skip_labels=True, prefer_last=False):
    """Find the first value reachable from the highest-priority anchor.

    Returns ``(value, confidence, line_index)`` or ``(None, 0.0, None)``.
    Anchors are tried in priority order across the whole document, so a
    strong label late in the page still beats a weak one at the top.

    ``prefer_last`` scans bottom-up within each anchor. Money labels need
    this: a column header reading "Total" sits above the item table, so a
    top-down scan captures the first quantity in that column instead of
    the summary figure at the foot of the invoice.
    """
    for candidate in _anchored_candidates(
        lines, anchors, parser, skip_labels, prefer_last
    ):
        return candidate
    return None, 0.0, None


def _anchored_candidates(lines, anchors, parser, skip_labels=True, prefer_last=False):
    """Yield every ``(value, confidence, line_index)`` an anchor can reach.

    Ordered best-first, so the first item is what _search_anchored returns.
    Callers that can cross-check values against each other (the money
    fields, via their arithmetic) use the rest of the list to recover when
    the best-ranked guess is wrong.
    """
    for pattern, weight in anchors:
        order = range(len(lines) - 1, -1, -1) if prefer_last else range(len(lines))
        for i in order:
            line = lines[i]
            text = line["text"]
            match = pattern.search(text)
            if not match:
                continue
            if skip_labels and NOT_AN_AMOUNT.search(text):
                continue

            # Value on the same line, after the label.
            tail = text[match.end():]
            value = parser(tail)
            if value is not None:
                yield value, weight * line["confidence"], i
                continue

            # Otherwise the next few lines (label above or beside value).
            for j in range(i + 1, min(i + 1 + LOOKAHEAD, len(lines))):
                nxt = lines[j]
                if skip_labels and NOT_AN_AMOUNT.search(nxt["text"]):
                    continue
                value = parser(nxt["text"])
                if value is not None:
                    # One step further away is slightly less trustworthy.
                    decay = 1.0 - 0.1 * (j - i - 1)
                    yield value, weight * nxt["confidence"] * decay, j
                    break


def _extract_vendor(lines):
    """Guess the vendor from the top of the document.

    The supplier's own name is almost always the first substantial piece
    of text on an invoice; a company suffix is a strong tell.
    """
    def is_own_company(text):
        lowered = text.lower()
        return any(name in lowered for name in OWN_COMPANY_NAMES)

    def rejected(text):
        return (
            len(text) < 3
            or VENDOR_NOISE.search(text)
            or is_own_company(text)
            # A company name is a short noun phrase, not a sentence.
            or len(text.split()) > 6
            or VENDOR_PROSE.search(text)
        )

    # Wide enough to reach a letterhead that sits below the customer's
    # address block, which is where some suppliers put their own name.
    head = lines[:25]
    for line in head:
        text = line["text"].strip()
        if rejected(text):
            continue
        if COMPANY_SUFFIX.search(text):
            return text, ANCHOR_STRONG * line["confidence"]

    for line in head:
        text = line["text"].strip()
        if rejected(text):
            continue
        # Needs to look like a name, not a reference or an amount.
        if not re.search(r"[A-Za-z]{3}", text):
            continue
        if parse_money(text) is not None or parse_date(text) is not None:
            continue
        return text, INFERRED * line["confidence"]
    return None, 0.0


def _extract_location(lines):
    """Work out which country the invoice comes from.

    Returns ``(country_name, currency_of_that_country, confidence)``, or
    ``(None, None, 0.0)``. The country is worth having in its own right,
    and it is also the only way to resolve a bare "$" or "Rs".

    A named country beats a city, because a city can appear in an address
    that is not the supplier's.

    The country mentioned *most often* wins rather than the one mentioned
    first. A line item can name a country it is not billed from -- one
    Claranet invoice lists "International Access Service - Germany" above
    its own UK address and phone number -- and taking the first match
    reports the wrong country. The supplier's own country recurs across
    letterhead, footer and registration details; an incidental one does
    not.
    """
    def currency_of(country):
        return next((c for p, (c, n) in COUNTRIES.items() if n == country), None)

    # --- the place, as printed on the invoice ---------------------------
    # A recognised town near the top of the page is the supplier's own,
    # and is what an expense record wants: "Chennai", not "India".
    # Every purchase invoice carries the buyer's address as well as the
    # supplier's, and the bill-to block often sits above the supplier's
    # own. Skipping it stops our own town being reported as the place the
    # expense happened -- the same confusion OWN_COMPANY_NAMES fixes for
    # the vendor name.
    skip = set()
    if OWN_COMPANY_NAMES:
        for i, line in enumerate(lines):
            lowered = line["text"].lower()
            if any(name in lowered for name in OWN_COMPANY_NAMES):
                skip.update(range(i, i + 5))

    location = None
    location_conf = 0.0
    city_country = None
    for i, line in enumerate(lines[:20]):
        if i in skip:
            continue
        match = CITY_PATTERN.search(line["text"])
        if match:
            # Report it in the list's canonical spelling, so "CHENNAI"
            # and "chennai" do not become two different locations.
            found = match.group(1)
            location = next(
                (c for c in CITY_COUNTRY if c.lower() == found.lower()), found
            )
            city_country = CITY_COUNTRY[location]
            location_conf = ANCHOR_STRONG * line["confidence"]
            break

    if location is None:
        # Not a town we know. The line following a street address is
        # almost always the town, so take it and strip any region after
        # the comma: "CHENNAI, TAMIL NADU." -> "Chennai".
        for i, line in enumerate(lines[:19]):
            if i in skip or i + 1 in skip:
                continue
            if not ADDRESS_MARKER.search(line["text"]):
                continue
            candidate = lines[i + 1]["text"].strip().rstrip(".,")
            candidate = candidate.split(",")[0].strip()
            if PLACE_SHAPE.match(candidate) and not NOT_A_PLACE.search(candidate):
                location = candidate.title()
                location_conf = INFERRED * lines[i + 1]["confidence"]
                break

    # --- the country, which is what resolves an ambiguous symbol -------
    tally = {}
    for line in lines:
        for pattern, (currency, name) in COUNTRIES.items():
            if re.search(pattern, line["text"], re.I):
                hits, best = tally.get(name, (0, 0.0))
                tally[name] = (hits + 1, max(best, line["confidence"]))

    country = None
    if tally:
        # A single passing mention is much weaker evidence than a country
        # that turns up throughout the document.
        country = max(tally.items(), key=lambda kv: kv[1][0])[0]
    else:
        # A tax registration scheme belongs to exactly one country, so it
        # outranks a city, which can belong to any address on the page.
        for line in lines:
            for pattern, name in TAX_ID_HINTS.items():
                if re.search(pattern, line["text"], re.I):
                    country = name
                    break
            if country:
                break
        if country is None:
            country = city_country

    return location, location_conf, country, currency_of(country) if country else None


def _extract_currency(lines, country_currency=None):
    """Identify the invoice currency.

    Signals in descending order of reliability: an explicit ISO code, a
    symbol that only one currency uses, a currency written as a word,
    then an ambiguous symbol resolved against the country. An ambiguous
    symbol with no country to resolve it falls back to the most common
    reading, flagged as inferred so the reviewer can see it was a guess.
    """
    # 1. An explicit ISO code needs no interpretation.
    for line in lines:
        upper = line["text"].upper()
        for code in CURRENCY_CODES:
            if re.search(rf"\b{code}\b", upper):
                return ("CNY" if code == "RMB" else code), ANCHOR_STRONG * line["confidence"]

    # 2. Symbols only one currency uses, and prefixed forms like A$.
    for line in lines:
        text = line["text"]
        for pattern, code in QUALIFIED_SYMBOL_RE.items():
            if pattern.search(text):
                return code, ANCHOR_STRONG * line["confidence"]
        for symbol, code in CURRENCY_SYMBOLS.items():
            if symbol in text:
                return code, ANCHOR_STRONG * line["confidence"]

    # 3. The currency spelled out.
    for line in lines:
        for pattern, code in CURRENCY_WORDS.items():
            if re.search(pattern, line["text"], re.I):
                return code, ANCHOR_WEAK * line["confidence"]

    # 4. A shared symbol, resolved by where the invoice is from.
    for line in lines:
        text = line["text"]
        for symbol, candidates in AMBIGUOUS_SYMBOLS.items():
            if not AMBIGUOUS_SYMBOL_RE[symbol].search(text):
                continue
            if country_currency in candidates:
                return country_currency, ANCHOR_STRONG * line["confidence"]
            # A bare letter is too weak to guess from on its own -- "R"
            # occurs constantly in reference numbers. Only trust it when
            # the country agrees, which the branch above already covers.
            if symbol.isalpha() and len(symbol) == 1:
                continue
            return candidates[0], INFERRED * line["confidence"]

    # 5. Nothing in the text, but the country still implies a currency.
    if country_currency:
        return country_currency, INFERRED

    # Deliberately not defaulting to any currency: guessing GBP on an
    # invoice from anywhere else is worse than admitting we do not know.
    return None, 0.0


def _extract_line_items(lines):
    """Reconstruct table rows from the flat reading-order stream.

    Because detections are already ordered left-to-right within a visual
    row, an item row shows up as a description entry followed by a short
    run of numeric entries. That run is what we harvest.
    """
    start = 0
    for i, line in enumerate(lines):
        if ITEM_HEADER.search(line["text"]):
            start = i + 1
            # Step over the rest of the header row's cells.
            while start < len(lines) and HEADER_CELL.match(lines[start]["text"].strip()):
                start += 1
            break

    items = []
    i = start
    while i < len(lines):
        text = lines[i]["text"].strip()
        if ITEM_STOP.search(text) and parse_money(text) is None:
            break

        # A description has letters and is not itself a bare number. It may
        # still contain digits ("Steel brackets 40mm"), which is why this
        # tests the whole cell rather than searching inside it.
        if len(text) >= 3 and re.search(r"[A-Za-z]{3}", text) and not IS_PURE_MONEY.match(text):
            numbers = []
            j = i + 1
            while j < len(lines) and len(numbers) < 4:
                candidate = lines[j]["text"].strip()
                if not IS_PURE_MONEY.match(candidate):
                    break
                value = parse_money(candidate)
                if value is None:
                    break
                numbers.append(value)
                j += 1

            if len(numbers) >= 2:
                quantity = unit_price = line_total = None
                if len(numbers) >= 3:
                    quantity, unit_price, line_total = numbers[0], numbers[-2], numbers[-1]
                else:
                    unit_price, line_total = numbers[0], numbers[1]
                    # Some layouts put the QTY column left of the
                    # description, so it arrives before it in reading
                    # order rather than after.
                    if i > start:
                        previous = lines[i - 1]["text"].strip()
                        if IS_PURE_MONEY.match(previous):
                            quantity = parse_money(previous)
                items.append(
                    {
                        "description": text,
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "line_total": line_total,
                    }
                )
                i = j
                continue
        i += 1
    return items


MONEY_CANDIDATE_LIMIT = 12


def _reconcile_money(lines, current):
    """Pick the subtotal/tax/total triple that actually adds up.

    On long documents the best-ranked label is often the wrong one --
    BT's bill repeats "Total" throughout 300 lines of call detail. Where
    a candidate combination satisfies subtotal + tax == total, that
    agreement is far stronger evidence than any label's wording, so it
    wins. ``current`` is the best-ranked guess per field, used unchanged
    when nothing reconciles.
    """
    if all(current[key][0] is not None for key in current):
        subtotal, tax, total = (current[k][0] for k in ("subtotal", "tax_amount", "total_amount"))
        if abs(subtotal + tax - total) <= 0.02:
            return None

    pools = {}
    for key, anchors in (
        ("subtotal", SUBTOTAL_ANCHORS),
        ("tax_amount", TAX_ANCHORS),
        ("total_amount", TOTAL_ANCHORS),
    ):
        seen = {}
        for value, conf, _ in _anchored_candidates(
            lines, anchors, find_money, prefer_last=True
        ):
            if value is not None and value not in seen:
                seen[value] = conf
            if len(seen) >= MONEY_CANDIDATE_LIMIT:
                break
        pools[key] = list(seen.items())

    if not pools["total_amount"]:
        return None
    # Line-level figures balance just as well as the grand total but are
    # orders of magnitude smaller. Without this floor, DHL's 800-line bill
    # reconciles to a confident 0.50.
    largest_total = max(value for value, _ in pools["total_amount"])
    floor = 0.1 * largest_total

    best = None
    for subtotal, subtotal_conf in pools["subtotal"]:
        for tax, tax_conf in pools["tax_amount"]:
            for total, total_conf in pools["total_amount"]:
                if total < floor or total <= 0:
                    continue
                difference = abs(subtotal + tax - total)
                if difference > 0.02:
                    continue
                # Exact agreement beats near agreement; a zero-tax triple
                # balances trivially whenever subtotal equals total, so it
                # ranks below one where the tax line genuinely fits.
                score = (
                    difference < 0.005,
                    tax != 0,
                    subtotal_conf + tax_conf + total_conf,
                )
                if best is None or score > best[0]:
                    best = (
                        score,
                        {
                            "subtotal": (subtotal, subtotal_conf),
                            "tax_amount": (tax, tax_conf),
                            "total_amount": (total, total_conf),
                        },
                    )
    return best[1] if best else None


def _label_kind(text):
    """Classify a summary label as subtotal / tax / total, if it is one."""
    for kind, anchors in (
        ("subtotal", SUBTOTAL_ANCHORS),
        ("tax_amount", TAX_ANCHORS),
        ("total_amount", TOTAL_ANCHORS),
    ):
        for pattern, _ in anchors:
            if pattern.search(text):
                return kind
    return None


def _extract_summary_block(lines):
    """Pair a run of stacked summary labels with the run of values below it.

    Some invoices stack the labels in one column and the figures in
    another ("Net Amount" / "VAT Amount" / "Total Payment", then the three
    amounts). Reading order emits every label before any value, so
    per-label lookahead hands all three the same first number. Matching
    the two runs by position recovers the real pairing.

    Returns ``{field: (value, confidence)}`` for whatever it can pair.
    """
    found = {}
    i = 0
    while i < len(lines):
        labels = []
        j = i
        while j < len(lines):
            text = lines[j]["text"].strip()
            if NOT_AN_AMOUNT.search(text) or find_money(text) is not None:
                break
            kind = _label_kind(text)
            if kind is None:
                break
            labels.append((kind, lines[j]["confidence"]))
            j += 1

        if len(labels) >= 2:
            values = []
            k = j
            while k < len(lines) and len(values) < len(labels):
                text = lines[k]["text"].strip()
                if IS_PURE_MONEY.match(text):
                    value = parse_money(text)
                elif CURRENCY_LINE.match(text):
                    value = find_money(text)
                else:
                    break
                if value is None:
                    break
                values.append((value, lines[k]["confidence"]))
                k += 1

            if len(values) == len(labels):
                for (kind, label_conf), (value, value_conf) in zip(labels, values):
                    # Keep the first pairing for a field; later blocks on
                    # multi-page documents tend to be per-page repeats.
                    found.setdefault(
                        kind, (value, ANCHOR_STRONG * min(label_conf, value_conf))
                    )
                i = k
                continue
        i = j + 1 if j > i else i + 1
    return found


def _apply_vendor_overrides(vendor_name, lines, fields, confidence):
    """Re-run extraction for a known-awkward vendor, if one is registered."""
    if not vendor_name:
        return
    key = vendor_name.lower()
    rules = next(
        (rules for name, rules in VENDOR_OVERRIDES.items() if name in key), None
    )
    if not rules:
        return
    text_blob = "\n".join(line["text"] for line in lines)
    # Each field is coerced to the type the rest of the pipeline expects,
    # so an override cannot put a raw string into a money or date column.
    coerce = {
        "subtotal": parse_money,
        "tax_amount": parse_money,
        "total_amount": parse_money,
        "invoice_date": parse_date,
    }
    for field, patterns in rules.items():
        for pattern in patterns:
            match = pattern.search(text_blob)
            if not match:
                continue
            captured = match.group(1) if match.groups() else match.group(0)
            value = coerce.get(field, lambda text: text.strip())(captured)
            if value is not None:
                fields[field] = value
                confidence[field] = ANCHOR_STRONG
            break


def extract_fields(lines):
    """Extract structured invoice fields from reading-order OCR lines.

    ``lines`` is the list of ``{"text", "confidence"}`` dicts produced by
    run_ocr_on_image / run_ocr_on_pdf. Returns the field values plus a
    per-field confidence map.
    """
    lines = [dict(line) for line in lines if line.get("text")]
    confidence = {}

    # skip_labels keeps a "VAT Registration Number: GB 973 3946 77" line
    # sitting under an "INVOICE" heading from being read as the number.
    invoice_number, invoice_number_conf, _ = _search_anchored(
        lines, INVOICE_NUMBER_ANCHORS, parse_invoice_number
    )
    invoice_date, invoice_date_conf, _ = _search_anchored(
        lines, DATE_ANCHORS, parse_date, skip_labels=False
    )
    total_amount, total_conf, _ = _search_anchored(
        lines, TOTAL_ANCHORS, find_money, prefer_last=True
    )
    subtotal, subtotal_conf, _ = _search_anchored(
        lines, SUBTOTAL_ANCHORS, find_money, prefer_last=True
    )
    tax_amount, tax_conf, _ = _search_anchored(
        lines, TAX_ANCHORS, find_money, prefer_last=True
    )
    vendor_name, vendor_conf = _extract_vendor(lines)
    # Location is resolved first: it is what disambiguates a bare "$" or
    # "Rs", which several countries share.
    location, location_conf, country, country_currency = _extract_location(lines)
    currency, currency_conf = _extract_currency(lines, country_currency)

    money = {
        "subtotal": (subtotal, subtotal_conf),
        "tax_amount": (tax_amount, tax_conf),
        "total_amount": (total_amount, total_conf),
    }

    # A stacked label/value summary block is more trustworthy than
    # per-label lookahead, so let it win where it found a pairing.
    money.update(_extract_summary_block(lines))

    # Failing that, let the arithmetic pick a triple that balances.
    reconciled = _reconcile_money(lines, money)
    if reconciled:
        money.update(reconciled)

    subtotal, subtotal_conf = money["subtotal"]
    tax_amount, tax_conf = money["tax_amount"]
    total_amount, total_conf = money["total_amount"]

    fields = {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "vendor_name": vendor_name,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
        "currency": currency,
        "location": location,
        "country": country,
        "line_items": _extract_line_items(lines),
    }
    confidence = {
        "invoice_number": invoice_number_conf,
        "invoice_date": invoice_date_conf,
        "vendor_name": vendor_conf,
        "subtotal": subtotal_conf,
        "tax_amount": tax_conf,
        "total_amount": total_conf,
        "currency": currency_conf,
        "location": location_conf,
        # Derived from the same evidence as location, so it inherits its
        # score rather than claiming independent confidence.
        "country": location_conf if country else 0.0,
        "line_items": 0.0,
    }

    _apply_vendor_overrides(vendor_name, lines, fields, confidence)

    # Arithmetic agreement is independent evidence that all three money
    # fields were read correctly, so let it move their scores.
    if None not in (fields["subtotal"], fields["tax_amount"], fields["total_amount"]):
        expected = fields["subtotal"] + fields["tax_amount"]
        if abs(expected - fields["total_amount"]) <= 0.02:
            for key in ("subtotal", "tax_amount", "total_amount"):
                confidence[key] = min(1.0, confidence[key] * 1.15)
        else:
            for key in ("subtotal", "tax_amount", "total_amount"):
                confidence[key] *= 0.6

    if fields["line_items"]:
        confidence["line_items"] = INFERRED

    fields["field_confidence"] = {
        key: round(min(1.0, max(0.0, value)), 3) for key, value in confidence.items()
    }
    return fields
