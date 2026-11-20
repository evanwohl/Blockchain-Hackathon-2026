"""Build the historical fill tape from the original EIA workbooks."""

import argparse
import gzip
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from freightos import regional_diesel

SNAPSHOT = regional_diesel.SNAPSHOT.with_name("fill_prices.json.gz")
REGION_CODES = {"east": "R10", "midwest": "R20", "gulf": "R30", "rockies": "R40", "west": "R50"}


def build(directory):
    import xlrd

    weekly = json.loads(gzip.decompress(regional_diesel.SNAPSHOT.read_bytes()))
    start, end = (weekly["prices"][i]["date"] for i in (0, -1))

    def series(symbol, scale):
        source = weekly["sources"][symbol]
        raw = (directory / source["url"].rsplit("/", 1)[1]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError(f"Unexpected EIA workbook for {symbol}")
        book = xlrd.open_workbook(file_contents=raw)
        sheet = book.sheet_by_name("Data 1")
        if sheet.cell_value(1, 1) != symbol:
            raise ValueError(f"Unexpected EIA series for {symbol}")
        values = {}
        for i in range(3, sheet.nrows):
            stamp, price = sheet.row_values(i)
            if not isinstance(price, float):
                continue
            day = xlrd.xldate_as_datetime(stamp, book.datemode).date().isoformat()
            if start <= day <= end:
                units = Decimal(str(price)) * scale
                if int(units) != units or day in values:
                    raise ValueError(f"Invalid {symbol} observation for {day}")
                values[day] = int(units)
        return values

    oil = series("RWTC", 100)
    diesel = {region: series(f"EMD_EPD2D_PTE_{code}_DPG", 1000) for region, code in REGION_CODES.items()}
    for row in weekly["prices"]:
        day = row["date"]
        if oil[day] != row["oil_cents"] or any(prices[day] != row["diesel_mills"][r] for r, prices in diesel.items()):
            raise ValueError(f"Fill prices differ from regional model for {day}")
    return {"sources": weekly["sources"], "oil_cents": oil, "diesel_mills": diesel}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook_directory", type=Path)
    data = build(parser.parse_args().workbook_directory)
    SNAPSHOT.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":"), sort_keys=True).encode(), mtime=0))
