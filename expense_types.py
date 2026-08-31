"""Suggest an expense type for a receipt.

Deliberately rule-based. A sentence-embedding model would need ~400MB
resident, and this service already peaks near its container limit
decoding a phone photo, so the accuracy would be paid for in crashes.
Rules cost nothing, run in under a millisecond, and can be explained --
"it matched 'unleaded'" is answerable in a way a cosine distance is not.

Two things follow from the category list itself:

Roughly a third of the leaves are not decidable from a receipt at all.
"Entertainment - Client" and "Entertainment - Staff" are the same
restaurant bill; the seventeen "Marketing -" leaves are the same
photographer's invoice. Where the leaf cannot be known, the category is
suggested and the caller offers its members -- that is why the result
carries a category as well as a type.

Nothing here keys on a specific supplier, because the suppliers are not
known yet. VENDOR_RULES exists for when they are: adding "greggs" ->
"Individual Meals" is a data change, not a code change.
"""

import re

# (label, GL code, category). Labels must match the front end's list
# exactly -- they are what gets selected in the dropdown.
EXPENSE_TYPES = [
    ("Airfare", "7-4320", "01. TRAVEL"),
    ("Airline Fees", "7-4300", "01. TRAVEL"),
    ("Car Rental", "6-3600", "01. TRAVEL"),
    # The front-end list gives no code for Hotel; left blank deliberately
    # rather than guessed at.
    ("Hotel", "", "01. TRAVEL"),
    ("Train", "7-4330", "01. TRAVEL"),
    ("Travel Christmas do", "7-4300", "01. TRAVEL"),

    ("Fuel", "6-3300", "02. TRANSPORTATION"),
    ("Parking", "7-4300", "02. TRANSPORTATION"),
    ("Public Transport", "7-4300", "02. TRANSPORTATION"),
    ("Taxi", "7-4300", "02. TRANSPORTATION"),
    ("Tolls/Road Charges", "7-4300", "02. TRANSPORTATION"),

    ("Breakfast", "7-4300", "03. MEALS AND ENTERTAINMENT"),
    ("Dinner", "7-4300", "03. MEALS AND ENTERTAINMENT"),
    ("Entertainment - Client", "7-4500", "03. MEALS AND ENTERTAINMENT"),
    ("Entertainment - Staff", "7-4600", "03. MEALS AND ENTERTAINMENT"),
    ("Entertainment - Supplier", "7-4400", "03. MEALS AND ENTERTAINMENT"),
    ("Individual Meals", "7-4340", "03. MEALS AND ENTERTAINMENT"),
    ("Lunch", "7-4300", "03. MEALS AND ENTERTAINMENT"),

    ("Computer Software", "7-4000", "04. OFFICE EXPENSES"),
    ("Courier/Shipping/Freight", "6-4100", "04. OFFICE EXPENSES"),
    ("Maintenance of Computer", "7-3900", "04. OFFICE EXPENSES"),
    ("Postage", "7-3500", "04. OFFICE EXPENSES"),
    ("Printing/Photocopying/Stationery", "7-3300", "04. OFFICE EXPENSES"),
    ("Repairs and Maintenance", "7-3800", "04. OFFICE EXPENSES"),

    ("Mobile Phone", "7-3200", "05. COMMUNICATIONS"),
    ("Telephone/Internet/Fax", "7-3200", "05. COMMUNICATIONS"),

    ("Bank Fees", "7-5900", "06. FEES"),
    ("Barclaycard Charges", "7-6000", "06. FEES"),
    ("Legal/ Professional Charges", "7-5400", "06. FEES"),
    ("Medical Fees", "7-2200", "06. FEES"),
    ("Passport/Visa Fees", "7-4300", "06. FEES"),
    ("Shopify Transaction Fees", "5-1500", "06. FEES"),
    ("Subscription & Donations", "6-1500", "06. FEES"),

    ("Advertising", "6-1100", "07. OTHER"),
    ("COS - Customer Penalties", "5-9550", "07. OTHER"),
    ("COS – Returns Fees", "5-7250", "07. OTHER"),
    ("Canteen", "7-3700", "07. OTHER"),
    ("Car Maintenance/Repairs", "6-3400", "07. OTHER"),
    ("Carriage Outwards", "6-4100", "07. OTHER"),
    ("Cleaning", "7-3600", "07. OTHER"),
    ("Exhibitions", "6-1300", "07. OTHER"),
    ("Gifts - Clients", "7-4400", "07. OTHER"),
    ("Gifts - Staff", "7-2200", "07. OTHER"),
    ("Incidentals Allowance", "7-4300", "07. OTHER"),
    ("Marketing - Collaborations", "6-1100", "07. OTHER"),
    ("Marketing - Hotel (Photography)", "7-4400", "07. OTHER"),
    ("Marketing - JD Support", "6-1100", "07. OTHER"),
    ("Marketing - Music", "6-1100", "07. OTHER"),
    ("Marketing - OOH Advertising", "6-1100", "07. OTHER"),
    ("Marketing - Other Misc", "7-3800", "07. OTHER"),
    ("Marketing - PR", "6-1100", "07. OTHER"),
    ("Marketing - Photography", "6-1100", "07. OTHER"),
    ("Marketing - Retail Support", "6-1100", "07. OTHER"),
    ("Marketing - Samples", "5-9000", "07. OTHER"),
    ("Marketing - Showroom Updates", "7-3800", "07. OTHER"),
    ("Marketing - Social media", "6-1100", "07. OTHER"),
    ("Marketing - Sponsorship - Tennis/Golf etc", "6-1400", "07. OTHER"),
    ("Marketing - Tennis Activation", "6-1100", "07. OTHER"),
    ("Marketing - Trade - Sales Docs, POS, Fairs", "6-1300", "07. OTHER"),
    ("Marketing - eCommerce Development", "6-1100", "07. OTHER"),
    ("Marketing - eCommerce Marketing", "6-1100", "07. OTHER"),
    ("Packaging", "5-4000", "07. OTHER"),
    ("Relocation Expenses", "7-1400", "07. OTHER"),
    ("Samples - Development Samples", "5-9003", "07. OTHER"),
    ("Security & Safety Charges", "7-4200", "07. OTHER"),
    ("Selling Costs", "7-8000", "07. OTHER"),
    ("Seminar/Course Fees", "7-1800", "07. OTHER"),
    ("Staff Awards/Incentives", "7-2200", "07. OTHER"),
    ("Staff Welfare", "7-2200", "07. OTHER"),
    ("Sundry Expenses", "7-4700", "07. OTHER"),
    ("Travel - other", "7-4300", "07. OTHER"),
    ("Tuition/Training Reimbursement", "7-1800", "07. OTHER"),
    ("Vehicle Road Tax", "6-3100", "07. OTHER"),
]

TYPE_BY_LABEL = {label: (code, category) for label, code, category in EXPENSE_TYPES}

# Trade vocabulary, not brand names: the words a business of this kind
# prints on its own paperwork. A weight of 3 is close to conclusive, 2 is
# a good indication, 1 is corroborating.
TYPE_KEYWORDS = {
    "Airfare": [(r"\bairfare|boarding\s*pass|\bpnr\b|passenger\s*name|e-?ticket|\bflight\b|departure.*arrival", 3),
                (r"\bairline|\bairways\b|\bairport\b", 2)],
    "Airline Fees": [(r"excess\s*baggage|baggage\s*fee|seat\s*selection|checked\s*bag", 3)],
    "Car Rental": [(r"car\s*(hire|rental)|rental\s*agreement|vehicle\s*hire|hire\s*period", 3),
                   (r"\bpick[- ]?up\b.*\bdrop[- ]?off\b", 2)],
    "Hotel": [(r"\bhotel\b|\bmotel\b|\binn\b|\bresort\b|room\s*(charge|rate|night)|night(?:'|')?s\s*stay|check[- ]?in.*check[- ]?out|accommodation|guest\s*name", 3),
              (r"\bsuite\b|\bbooking\b.*\bnights?\b", 2)],
    "Train": [(r"\btrain\b|\brail\b|railway|\bcoach\s*[A-Z]\b|platform|single\s*fare|return\s*fare|\bseat\b.*\bcoach\b", 3)],

    "Fuel": [(r"unleaded|\bdiesel\b|\bpetrol\b|\bgasolin|fuel\s*(type|grade)|pump\s*\d|litre|liters?\b.*@|per\s*l(?:it)?re", 3),
             (r"\bfuel\b|filling\s*station|service\s*station", 2)],
    "Parking": [(r"\bparking\b|car\s*park|\bbay\s*\d|entry\s*time.*exit\s*time|pay\s*(and|&)\s*display", 3)],
    "Public Transport": [(r"\bbus\s*(fare|ticket|pass)|\bmetro\b|\bsubway\b|\btram\b|underground|oyster|travel\s*card|zone\s*[12]", 3)],
    "Taxi": [(r"\btaxi\b|\bcab\b|private\s*hire|ride\s*fare|\bdrop\s*off\b.*\bpick\s*up\b|trip\s*(id|fare)|driver\s*name", 3),
             (r"\bfare\b|\bdistance\b.*\bkm\b|\bmiles\b.*\bfare\b", 1)],
    "Tolls/Road Charges": [(r"\btoll\b|congestion\s*charge|road\s*charge|\bdartford\b|\bmotorway\b", 3)],

    # Named on the receipt, these outrank the generic restaurant
    # vocabulary below: a bill that says "Lunch" is a lunch, even though
    # "bistro" and "table 12" also point at Individual Meals.
    "Breakfast": [(r"\bbreakfast\b", 4)],
    "Lunch": [(r"\blunch\b", 4)],
    "Dinner": [(r"\bdinner\b|\bsupper\b", 4)],
    "Individual Meals": [(r"restaurant|\bcaf[eé]\b|\bbistro\b|\bdiner\b|\bmeal\b|table\s*(no|number)?\s*\d|covers?\s*\d|food\s*bill|\bmenu\b|service\s*charge.*food", 3),
                         (r"\bkitchen\b|\bgrill\b|\bpizza\b|\bburger\b|\bcoffee\b|\btakeaway\b", 2)],

    "Computer Software": [(r"software\s*licen[cs]e|\blicen[cs]e\b.*\buser|subscription.*(software|licen|seat)|saas\b|\bhosting\b|\bdomain\b|cloud\s*service|\bapi\b\s*usage|user\s*licen", 3),
                          (r"\bsoftware\b|\bapp\b\s*subscription", 2)],
    "Courier/Shipping/Freight": [(r"\bcourier\b|\bfreight\b|\bconsignment\b|air\s*waybill|\bawb\b|tracking\s*(no|number)|shipment|waybill|delivery\s*note|\bparcel\b|express\s*worldwide", 3),
                                 (r"\bshipping\b|\bdispatch\b", 2)],
    "Maintenance of Computer": [(r"(laptop|desktop|\bpc\b|computer|hardware)\s*(repair|service|maintenance)|it\s*support\s*(call|visit)", 3)],
    "Postage": [(r"\bpostage\b|\bstamps?\b|royal\s*mail|recorded\s*delivery|first\s*class\s*post", 3)],
    "Printing/Photocopying/Stationery": [(r"stationer|photocopy|\bprinting\b|\btoner\b|\bcartridge\b|\bpaper\b\s*a4|business\s*cards|\bleaflets?\b|\bbinding\b", 3)],
    "Repairs and Maintenance": [(r"\brepair\b|\bmaintenance\b|\bservicing\b|\bcallout\b|\bcall[- ]out\b|labour\s*charge|parts\s*(and|&)\s*labour", 2)],

    "Mobile Phone": [(r"mobile\s*(phone|bill|number)|\bairtime\b|\bsim\b\s*card|data\s*(bundle|plan)|pay\s*monthly.*mobile", 3)],
    "Telephone/Internet/Fax": [(r"broadband|\bline\s*rental\b|\bfibre\b|\bleased\s*line\b|\bconnectivity\b|\bcalls?\s*charges?\b|\bwan\b|\bethernet\b", 3),
                               (r"\btelecom|\bbandwidth\b", 2),
                               # Weak on purpose: almost every invoice
                               # prints a "Telephone:" contact line, so
                               # the bare word says nothing about what
                               # was bought. A pest control invoice was
                               # being filed as a phone bill on this.
                               (r"\btelephone\b|\binternet\b|\bfax\b", 1)],

    "Bank Fees": [(r"bank\s*(charge|fee)|account\s*fee|overdraft|interest\s*charge|\bbacs\b\s*fee|transaction\s*fee.*bank", 3)],
    "Barclaycard Charges": [(r"barclaycard", 3)],
    "Legal/ Professional Charges": [(r"\blegal\b|\bsolicitor|\bbarrister|professional\s*(fee|charge|service)|\bconsultanc|\baccountanc|\baudit\s*fee|advisory\s*fee", 3)],
    "Medical Fees": [(r"\bmedical\b|\bclinic\b|\bhospital\b|\bpharmac|\bdoctor\b|\bdental\b|prescription|\bdiagnostic|\bsurgery\b|\bvaccin", 3)],
    "Passport/Visa Fees": [(r"\bpassport\b|\bvisa\s*(fee|application|appl)|\bimmigration\b|entry\s*permit|\betav?\b\s*fee", 3)],
    "Shopify Transaction Fees": [(r"shopify", 3)],
    "Subscription & Donations": [(r"\bsubscription\b|\bmembership\b|\bdonation\b|annual\s*fee.*member|\bcharity\b", 2)],

    "Advertising": [(r"\badvertis|\bad\s*spend\b|\bcampaign\b|\bmedia\s*buy", 2)],
    "Canteen": [(r"\bcanteen\b|staff\s*(catering|refreshment)|\bpantry\b", 3)],
    "Car Maintenance/Repairs": [(r"\bmot\b|\btyres?\b|\bservicing\b.*vehicle|vehicle\s*(repair|service)|\bgarage\b|\bbrake\b|\bexhaust\b", 3)],
    "Carriage Outwards": [(r"carriage\s*outwards?", 3)],
    "Cleaning": [(r"\bcleaning\b|\bjanitor|\bwaste\b|\brefuse\b|\bsanitat|\bhygiene\b|\bpest\s*control\b|\bskip\s*hire\b|\bbin\s*(collection|hire)\b|\bdisposal\b", 3)],
    "Exhibitions": [(r"\bexhibition\b|\btrade\s*show\b|\bstand\s*(hire|fee)\b|\bexpo\b", 3)],
    "Packaging": [(r"\bpackaging\b|\bcartons?\b|\bpolybags?\b|\bhangtags?\b|\bswing\s*tickets?\b|\blabels?\b.*\bgarment", 3)],
    "Relocation Expenses": [(r"\brelocation\b|\bremovals?\b|shipping\s*of\s*personal", 3)],
    "Security & Safety Charges": [(r"\bsecurity\b|\bcctv\b|\balarm\b|\bfire\s*(safety|extinguisher)\b|\bppe\b|\bguard(ing)?\b", 3)],
    "Seminar/Course Fees": [(r"\bseminar\b|\bcourse\s*fee\b|\bworkshop\b|\bconference\s*fee\b|\bdelegate\s*fee\b", 3)],
    "Tuition/Training Reimbursement": [(r"\btuition\b|\btraining\s*(fee|course|programme)\b|\bcertification\b", 3)],
    "Vehicle Road Tax": [(r"road\s*tax|vehicle\s*excise|\bdvla\b|\bv5c\b", 3)],
    "Marketing - Photography": [(r"\bphotograph|\bphoto\s*shoot\b|\bvideograph", 2)],
    "Marketing - Social media": [(r"\bsocial\s*media\b|\binstagram\b|\bfacebook\s*ads\b|\btiktok\b|\binfluencer\b", 2)],
    "Marketing - PR": [(r"\bpublic\s*relations\b|\bpr\s*agency\b|\bpress\s*release\b", 2)],
}

# Filled in by whoever deploys this, once the real suppliers are known.
# A vendor match is decisive: it outranks every keyword. Keys are matched
# case-insensitively as substrings of the vendor name.
#     VENDOR_RULES = {"greggs": "Individual Meals", "shell": "Fuel"}
VENDOR_RULES = {}

# Suppliers whose category is the same the world over. These are not
# client-specific guesses -- a receipt from Uber is a taxi fare whoever
# is claiming it.
#
# Distinctive enough to recognise anywhere on the page: no ordinary
# English sentence contains them.
DISTINCTIVE_BRANDS = {
    r"\buber\s*eats\b|\bdeliveroo\b|\bdoordash\b": "Individual Meals",
    r"\buber\b|\blyft\b|\bpickme\b": "Taxi",
    r"\btexaco\b|\bcaltex\b|\bceypetco\b|\bindian\s*oil\b": "Fuel",
    r"\bmarriott\b|\bhilton\b|\bpremier\s*inn\b|\btravelodge\b|\bhyatt\b"
    r"|\bradisson\b|\bcinnamon\s*grand\b|\bbooking\.com\b|\bexpedia\b"
    r"|\bagoda\b|\bairbnb\b": "Hotel",
    r"\btrainline\b|\bnational\s*rail\b|\birctc\b": "Train",
    r"\beasyjet\b|\bryanair\b|\bbritish\s*airways\b|\bqatar\s*airways\b"
    r"|\bsrilankan\b": "Airfare",
    r"\bdhl\b|\bfedex\b|\baramex\b|\bevri\b": "Courier/Shipping/Freight",
    r"\broyal\s*mail\b": "Postage",
    r"\bmicrosoft\b|\badobe\b|\bgoogle\s*(cloud|workspace)\b|\bamazon\s*web\b"
    r"|\batlassian\b|\bdropbox\b|\bgithub\b": "Computer Software",
    r"\bshopify\b": "Shopify Transaction Fees",
    r"\bbarclaycard\b": "Barclaycard Charges",
    r"\bvodafone\b|\bairtel\b|\bmobitel\b": "Mobile Phone",
    r"\bvirgin\s*media\b|\btalktalk\b|\bclaranet\b": "Telephone/Internet/Fax",
    r"\bbiffa\b|\bveolia\b": "Cleaning",
}

# Brand names that are also ordinary words. Searching the body text for
# these is actively harmful: "To cover three months" became a mobile
# phone bill, and "User Bolt-On Type" became a taxi fare. They are only
# consulted against the supplier's own name, where a brand belongs.
AMBIGUOUS_BRANDS = {
    r"\bbolt\b|\bola\b|\bgrab\b": "Taxi",
    r"\bjust\s*eat\b": "Individual Meals",
    r"\bshell\b|\bbp\b|\besso\b": "Fuel",
    r"\bibis\b": "Hotel",
    r"\bemirates\b|\bindigo\b|\bsncf\b": "Airfare",
    r"\bups\b|\btnt\b|\bdpd\b|\bhermes\b": "Courier/Shipping/Freight",
    r"\baws\b|\bslack\b|\bzoom\b": "Computer Software",
    r"\bo2\b|\bee\b|\bthree\b|\bdialog\b": "Mobile Phone",
    r"\bbt\b|\bsky\b|\bslt\b": "Telephone/Internet/Fax",
    r"\bsuez\b": "Cleaning",
}

# Leaves a receipt can never distinguish between: the paperwork is
# identical and only the claimant knows which applies. Suggesting the
# category and letting them choose is honest; guessing is not.
UNDECIDABLE_PREFIXES = ("Marketing - ", "Entertainment - ", "Gifts - ", "COS ")


def _score(text, vendor):
    """Accumulate keyword weight per expense type."""
    scores = {}
    vendor_text = (vendor or "").lower()
    for label, rules in TYPE_KEYWORDS.items():
        total = 0
        for pattern, weight in rules:
            if re.search(pattern, text, re.I):
                total += weight
            # The same word in the supplier's own name means much more
            # than in the body: "Hotel" in "Grand Hotel" identifies the
            # business, whereas in a line item it may just be an address.
            if vendor_text and re.search(pattern, vendor_text, re.I):
                total += weight * 2
        if total:
            scores[label] = total
    return scores


def classify_expense(lines, vendor_name=None, top_n=3):
    """Suggest an expense type for an OCR'd receipt.

    Returns the best guess, its GL code and category, a confidence, and
    the runners-up -- so a review UI can pre-select one and still offer
    the plausible alternatives without scrolling seventy-odd options.

    ``expense_type`` is None when nothing scores: an unrecognised receipt
    should leave the field empty rather than assert a wrong category.
    """
    text = "\n".join(line["text"] for line in lines)
    vendor = vendor_name or ""

    def result(label, confidence, alternatives=()):
        code, category = TYPE_BY_LABEL.get(label, ("", None))
        return {
            "expense_type": label,
            "expense_code": code,
            "expense_category": category,
            "expense_type_confidence": round(min(1.0, confidence), 3),
            "expense_type_alternatives": list(alternatives),
        }

    # 1. A configured supplier is decisive.
    haystack = f"{vendor}\n{text}".lower()
    for needle, label in VENDOR_RULES.items():
        if needle.lower() in haystack:
            return result(label, 1.0)

    # 2. The supplier's own name, which is where a brand belongs. Both
    #    lists are consulted here, ambiguous ones included.
    if vendor:
        for brands in (DISTINCTIVE_BRANDS, AMBIGUOUS_BRANDS):
            for pattern, label in brands.items():
                if re.search(pattern, vendor, re.I):
                    return result(label, 0.95)

    # 3. A distinctive brand anywhere on the page. Only the unmistakable
    #    ones: the vendor line is often misread, so the body is a useful
    #    fallback, but an everyday word found there means nothing.
    for pattern, label in DISTINCTIVE_BRANDS.items():
        if re.search(pattern, text, re.I):
            return result(label, 0.85)

    # 3. Trade vocabulary.
    scores = _score(text, vendor)
    if not scores:
        return result(None, 0.0)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_label, best_score = ranked[0]

    # Confidence rises with the weight of evidence and falls when a
    # runner-up scores nearly as well.
    confidence = min(0.85, 0.35 + 0.1 * best_score)
    if len(ranked) > 1:
        runner_up = ranked[1][1]
        if runner_up >= best_score:
            confidence *= 0.7

    alternatives = [label for label, _ in ranked[1:top_n]]

    # Where the winning leaf is one of a set the receipt cannot separate,
    # offer its siblings rather than pretending to know which.
    prefix = next(
        (p for p in UNDECIDABLE_PREFIXES if best_label.startswith(p)), None
    )
    if prefix:
        # Siblings sharing the prefix, not the whole category: the
        # seventeen "Marketing -" leaves are the useful alternatives to a
        # photographer's invoice, whereas "07. OTHER" holds forty
        # unrelated ones and offering the first few is no help.
        alternatives = [
            label for label, _, _ in EXPENSE_TYPES
            if label.startswith(prefix) and label != best_label
        ][: top_n - 1]
        confidence *= 0.6

    return result(best_label, confidence, alternatives)
