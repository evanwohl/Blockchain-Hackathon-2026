"""Read a rate confirmation into the fields of the one-load cash calendar.

The parser only proposes values. Each one carries the document line it came from,
and the driver confirms or corrects it in the form before anything is priced.
"""
import io
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

MAX_BYTES = 5 * 1024 * 1024
DEFAULT_MPG = Decimal("6.5")
TEXT_TYPES = {"text/plain"}
PDF_TYPES = {"application/pdf"}
IMAGE_TYPES = {"image/png", "image/jpeg"}

MONEY = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?(?!\s*(?:%|mi|miles|lbs|gal))", re.I)
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATE = re.compile(
    r"(\d{4}-\d{2}-\d{2})"
    r"|(\d{1,2}/\d{1,2}/\d{2,4})"
    rf"|((?:{MONTHS})[a-z]*\.?\s+\d{{1,2}},?\s+\d{{4}})", re.I)

LABELS = {
    "freight": re.compile(r"line\s*-?\s*haul|base\s+rate|flat\s+rate|freight\s+(?:rate|charge|pay)|carrier\s+(?:rate|pay)", re.I),
    "surcharge": re.compile(r"fuel\s+surcharge|\bfsc\b|fuel\s+(?:adj|adjustment)", re.I),
    "total": re.compile(r"total\s+(?:rate|pay|amount|carrier\s+pay|charges?)|\btotal\b", re.I),
    "pickup": re.compile(r"pick\s*-?\s*up|ship\s+date|shipper", re.I),
    "delivery": re.compile(r"deliver(?:y)?|consignee|drop\s*-?\s*off", re.I),
    "load_id": re.compile(r"(?:load|order|pro|confirmation|reference|ref)\s*(?:#|no\.?|number|id)\s*:?\s*([A-Z0-9][A-Z0-9-]{2,})", re.I),
    "terms": re.compile(r"net\s*-?\s*(\d{1,3})|(\d{1,3})\s+days?\s+(?:after|from|net)|quick\s*-?\s*pay", re.I),
    "miles": re.compile(r"(\d{1,3}(?:,\d{3})+|\d{2,5})\s*(?:loaded\s+)?(?:mi\b|miles)", re.I),
    "mpg": re.compile(r"(\d{1,2}(?:\.\d)?)\s*mpg", re.I),
    "carrier": re.compile(r"^\s*carrier(?:\s+name)?\s*:\s*(.+)$", re.I),
    "broker": re.compile(r"^\s*(?:broker|shipper\s+of\s+record|customer|bill\s+to)(?:\s+name)?\s*:\s*(.+)$", re.I),
}


class DocumentError(ValueError):
    """The upload cannot be read; the driver should enter the values manually."""


def extract_text(raw, content_type):
    if not raw:
        raise DocumentError("The file is empty")
    if len(raw) > MAX_BYTES:
        raise DocumentError("Upload a file under 5 MB")
    if content_type in TEXT_TYPES:
        return raw.decode("utf-8", errors="replace")
    if content_type in PDF_TYPES:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
        try:
            reader = PdfReader(io.BytesIO(raw))
            text = "\n".join(page.extract_text() or "" for page in reader.pages[:10])
        except (PdfReadError, ValueError, KeyError, TypeError) as exc:
            raise DocumentError("This PDF could not be read") from exc
        if not text.strip():
            raise DocumentError("This PDF has no text layer. Upload the original PDF or enter the values below")
        return text
    if content_type in IMAGE_TYPES:
        return _ocr(raw, ".png" if content_type == "image/png" else ".jpg")
    raise DocumentError("Upload a PDF, PNG, JPG or text file")


def _ocr(raw, suffix):
    binary = shutil.which("tesseract")
    if not binary:
        raise DocumentError("Reading photos needs Tesseract OCR on this computer. Upload the PDF or enter the values below")
    with tempfile.NamedTemporaryFile(suffix=suffix) as image:
        image.write(raw)
        image.flush()
        try:
            result = subprocess.run([binary, image.name, "stdout"], capture_output=True, timeout=60, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            raise DocumentError("The photo could not be read") from exc
    return result.stdout.decode("utf-8", errors="replace")


def _amount(text):
    for match in MONEY.finditer(text):
        whole, cents = match.group(1), match.group(2) or ""
        if not match.group(0).lstrip().startswith("$") and not cents and "," not in whole:
            continue  # bare integers are usually IDs, weights or miles
        try:
            value = Decimal(whole.replace(",", "") + cents)
        except InvalidOperation:
            continue
        if value > 0:
            return value
    return None


def _date(text):
    match = DATE.search(text)
    if not match:
        return None
    raw = match.group(0).replace(".", "").replace(",", "")
    raw = re.sub(r"\s+", " ", raw)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(raw.title() if raw[0].isalpha() else raw, fmt).date()
        except ValueError:
            continue
    if raw[:4].lower() == "sept":
        return _date("Sep" + raw[4:])
    return None


def _find(lines, label, reader, lookahead=2):
    """Return (value, line) for the first labeled line whose value is on it or just below it."""
    for index, line in enumerate(lines):
        if not LABELS[label].search(line):
            continue
        for candidate in lines[index:index + 1 + lookahead]:
            tail = candidate[LABELS[label].search(candidate).end():] if candidate is line else candidate
            value = reader(tail)
            if value is not None:
                return value, " ".join(candidate.split())
    return None, None


def parse(text):
    lines = [line for line in text.splitlines() if line.strip()]
    fields, evidence, notes = {}, [], []

    def keep(field, label, value, line, shown=None):
        fields[field] = value
        evidence.append(dict(field=field, label=label, value=shown or value, line=line))

    freight, freight_line = _find(lines, "freight", _amount)
    surcharge, surcharge_line = _find(lines, "surcharge", _amount)
    total, total_line = _find(lines, "total", _amount)
    if freight is None and total is not None:
        freight = total - (surcharge or 0)
        freight_line = total_line
        notes.append("Freight amount is the total less any fuel surcharge.")
    if freight is not None and freight > 0:
        keep("freight_cents", "Freight amount", f"{freight:.2f}", freight_line, f"${freight:,.2f}")
    if surcharge is not None:
        keep("surcharge_cents", "Fuel surcharge", f"{surcharge:.2f}", surcharge_line, f"${surcharge:,.2f}")

    pickup, pickup_line = _find(lines, "pickup", _date, lookahead=3)
    delivery, delivery_line = _find(lines, "delivery", _date, lookahead=3)
    if pickup:
        keep("fuel_date", "Buy fuel on (pickup date)", pickup.isoformat(), pickup_line)

    net_days, terms_line = None, None
    for line in lines:
        match = LABELS["terms"].search(line)
        if match and (match.group(1) or match.group(2)):
            net_days, terms_line = int(match.group(1) or match.group(2)), " ".join(line.split())
            break
    if any(re.search(r"quick\s*-?\s*pay", line, re.I) for line in lines):
        notes.append("Quick pay is mentioned; compare it as a customer early-payment offer, not the default date.")
    if delivery:
        if net_days is None:
            net_days = 30
            notes.append("No payment terms found; assumed Net 30 from delivery.")
        due = (delivery + timedelta(days=net_days)).isoformat()
        source = f"{delivery_line} · {terms_line or 'assumed Net 30'}"
        keep("freight_date", f"Expected freight payment (delivery + {net_days} days)", due, source)
        if "surcharge_cents" in fields:
            keep("surcharge_date", f"Expected surcharge payment (delivery + {net_days} days)", due, source)

    miles, miles_line = _scan(lines, "miles")
    mpg, _ = _scan(lines, "mpg")
    if miles:
        rate = mpg or DEFAULT_MPG
        gallons = (miles / rate).quantize(Decimal("0.01"))
        keep("gallon_hundredths", f"US gallons ({miles:,} mi at {rate} MPG)", f"{gallons:.2f}", miles_line)
        if not mpg:
            notes.append(f"Gallons assume {DEFAULT_MPG} MPG; correct it if your truck differs.")

    load_id = next((m.group(1) for line in lines if (m := LABELS["load_id"].search(line))), None)
    parties = {}
    for role in ("carrier", "broker"):
        match = next((m for line in lines if (m := LABELS[role].search(line))), None)
        if match:
            parties[role] = match.group(1).strip()[:80]

    wanted = {"freight_cents": "freight amount", "freight_date": "delivery date",
              "fuel_date": "pickup date", "surcharge_cents": "fuel surcharge"}
    missing = [label for field, label in wanted.items() if field not in fields]
    return dict(load_id=load_id, parties=parties, fields=fields, evidence=evidence,
                missing=missing, notes=notes)


def _scan(lines, label):
    for line in lines:
        value = _number(LABELS[label], line)
        if value is not None:
            return value, " ".join(line.split())
    return None, None


def _number(pattern, text):
    match = pattern.search(text)
    if not match:
        return None
    try:
        value = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def read(raw, content_type):
    result = parse(extract_text(raw, content_type))
    if not result["fields"]:
        raise DocumentError("No rate, dates or surcharge were found. Check the document type or enter the values below")
    return result
