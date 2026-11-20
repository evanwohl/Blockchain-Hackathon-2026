import io
import json
from unittest.mock import Mock

import pytest

from freightos import rate_confirmation as ratecon
from freightos.app import Handler
from freightos.capture import ROOT
from freightos.load_calendar import project

SAMPLE = (ROOT / "artifacts/rate_confirmation/sample.txt").read_bytes()


def pdf(lines):
    """A minimal single-page PDF whose text layer holds the given lines."""
    text = "BT /F1 11 Tf 14 TL 50 750 Td " + " ".join(
        "(" + line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(text)} >>\nstream\n{text}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n{body}\nendobj\n".encode())
    start = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    out.write("".join(f"{o:010d} 00000 n \n" for o in offsets).encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode())
    return out.getvalue()


def test_sample_rate_confirmation_fills_the_load_calendar():
    result = ratecon.read(SAMPLE, "text/plain")
    assert result["load_id"] == "LFB-20481"
    assert result["parties"]["broker"] == "Longhorn Freight Brokerage LLC"
    assert result["fields"] == {
        "freight_cents": "3000.00", "surcharge_cents": "180.00", "fuel_date": "2026-11-05",
        "freight_date": "2026-11-28", "surcharge_date": "2026-11-28",
        "gallon_hundredths": "200.00"}
    assert result["missing"] == []
    assert any("6.5 MPG" in note for note in result["notes"])
    assert all(item["line"] for item in result["evidence"])

    # The proposed fields drop straight into the existing projection without double-counting.
    fields = result["fields"]
    case = dict(opening_date="2026-11-01", opening_cents=60000, floor_cents=10000,
                fuel_date=fields["fuel_date"], gallon_hundredths=20000, pump_mills=3725,
                freight_cents=300000, freight_date=fields["freight_date"], freight_status="expected",
                surcharge_cents=18000, surcharge_date=fields["surcharge_date"], surcharge_status="expected")
    rows = project(case)["rows"]
    assert rows[-1]["balance_cents"] == 60000 - 74500 + 300000 + 18000


def test_pdf_text_layer_and_total_only_documents():
    doc = pdf(["Rate Confirmation", "Order #: ACME-77", "Ship Date: Nov 3, 2026", "Delivery: 2026-11-06",
               "Fuel Surcharge: $210.50", "Total Rate: $2,710.50", "Quick Pay available", "Net 15"])
    result = ratecon.read(doc, "application/pdf")
    assert result["load_id"] == "ACME-77"
    assert result["fields"]["freight_cents"] == "2500.00"
    assert result["fields"]["surcharge_cents"] == "210.50"
    assert result["fields"]["fuel_date"] == "2026-11-03"
    assert result["fields"]["freight_date"] == "2026-11-21"
    assert any("Quick pay" in note for note in result["notes"])
    assert any("total less" in note for note in result["notes"])
    assert "gallon_hundredths" not in result["fields"]


def test_missing_terms_assume_net_30_and_are_flagged():
    result = ratecon.parse("Linehaul $1,000.00\nPickup 11/01/2026\nDelivery 11/02/26\n")
    assert result["fields"]["freight_date"] == "2026-12-02"
    assert "surcharge_date" not in result["fields"]
    assert result["missing"] == ["fuel surcharge"]
    assert any("Net 30" in note for note in result["notes"])


@pytest.mark.parametrize("raw, kind, message", [
    (b"", "text/plain", "empty"),
    (b"hello", "application/zip", "PDF, PNG, JPG"),
    (b"not a pdf", "application/pdf", "could not be read"),
    (b"Weight 42000 lbs\nNothing else", "text/plain", "No rate"),
])
def test_unreadable_uploads_ask_for_manual_entry(raw, kind, message):
    with pytest.raises(ratecon.DocumentError, match=message):
        ratecon.read(raw, kind)


def test_upload_route_returns_fields_or_a_readable_error():
    def post(body, kind):
        handler = Mock(spec=Handler)
        handler.path = "/api/rate-confirmation"
        handler.headers = Mock()
        handler.headers.get.side_effect = lambda key, default=None: str(len(body)) if key == "Content-Length" else default
        handler.headers.get_content_type.return_value = kind
        handler.rfile = io.BytesIO(body)
        Handler.read_rate_confirmation(handler)
        status, payload = handler.respond.call_args.args
        return status, json.loads(json.dumps(payload))

    status, payload = post(SAMPLE, "text/plain")
    assert status == 200 and payload["fields"]["freight_cents"] == "3000.00"
    status, payload = post(b"x", "application/zip")
    assert status == 400 and "PDF" in payload["error"]


def test_load_page_offers_the_upload_and_sample():
    def read(path):
        handler = Mock(spec=Handler)
        handler.path = path
        Handler.do_GET(handler)
        status, _, body = handler.reply.call_args.args
        assert status == 200
        return body

    assert b'id="ratecon"' in read("/load")
    assert read("/rate-confirmation-sample.txt") == SAMPLE
    assert b"/api/rate-confirmation" in read("/load.js")
