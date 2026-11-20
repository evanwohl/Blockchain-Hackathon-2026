import base64
import gzip
import json
from copy import deepcopy

import pytest

from freightos import fuel_floor as floor


def test_whole_cent_minimum_replays_both_sides_of_the_boundary():
    data = json.loads(gzip.decompress(floor.SAMPLE.read_bytes()))
    q = floor.replay(data)
    assert q == floor.saved()
    result = floor.decisions(q, [20000, 19950, 15000], 300, [4984, 15000])
    best = result["by_receipt"]["20000"]
    assert (best["separate_cents"], best["untouched_cents"], best["vendor_cents"], best["least_cents"]) == (5432, 2068, 13568, 300)
    assert best["one_cent_less"]["least_cents"] == 299
    assert best["payment_slot"] >= result["slot"]
    market = q["market"]
    adjacent, = market["checks"]["13569"]
    principal = 20000 - 19000 + adjacent["swap_raw"] // market["scale"]
    assert principal - floor.ceil_div((adjacent["limit_lamports"] + adjacent["fee_lamports"])*15000, 10000000) == 299
    below = floor.best_split(market, 5431, 20000, 300, 15000)
    assert below["least_cents"] == 299
    reduced = result["by_receipt"]["19950"]
    assert reduced["separate_cents"] == 6248 and reduced["payment_slot"] is None
    assert result["by_receipt"]["15000"]["least_cents"] == -4574
    assert floor.decisions(q, [20000], 0, [4984,10735])["by_receipt"]["20000"]["separate_cents"] == 3528


def test_search_handles_cash_only_network_fee_and_breaking_the_liquidity_range():
    m = {**floor.saved()["market"], "cap_lamports": 0}
    assert floor.best_split(m, 18999, 20000, 0, 0) is None
    choice = floor.best_split(m, 20000, 20000, 1000, 50000)
    assert choice["vendor_cents"] == choice["restore_lamports"] == 0 and choice["least_cents"] == 1000
    m["cap_lamports"] = m["fee_lamports"]
    assert floor.best_split(m, 19000, 20000, 0, 0)["separate_cents"] == 16957
    c = dict(m["curves"][0],max_raw=98)
    with pytest.raises(ValueError, match="range"):
        floor.swap_cost(c,99)
    with pytest.raises(ValueError, match="range"):
        floor.best_split({**m,"curves":[c]},7500,20000,300,15000)


def test_wrong_token_pair_tick_array_and_moving_endpoint_are_rejected():
    data = json.loads(gzip.decompress(floor.SAMPLE.read_bytes()))
    row = next(r for r in data["calls"] if r["method"] == "getMultipleAccounts" and len(r["params"][0]) == 4 and r["params"][0][0] == str(floor.fuel_swap.POOLS[0]))
    for index, offset in ((0,101), (1,-1), (0,43)):
        accounts = deepcopy(row["response"])
        a = accounts["value"][index]["data"]
        raw = bytearray(base64.b64decode(a[0])); raw[offset] ^= 1
        a[0] = base64.b64encode(raw).decode()
        start = data["calls"].index(row)-2
        calls = [*data["calls"][start:start+2], {**row,"response":accounts}]
        with pytest.raises(ValueError, match="Unsupported"):
            floor.atomic_fuel.replay_calls(calls, lambda read: floor.read_curves(calls[0]["params"][1]["minContextSlot"],read))
    quote = deepcopy(floor.saved())
    quote["routes"][1]["spent_lamports"] += 1
    with pytest.raises(ValueError, match="endpoint"):
        floor.verified_curves(quote)


def test_live_curve_failure_keeps_the_two_public_endpoint_quotes(monkeypatch):
    from freightos import fuel_cash

    evidence = json.loads(gzip.decompress(fuel_cash.SAMPLE.read_bytes()))

    def unsupported(slot, rpc):
        raise ValueError("The current tick array is unsupported")

    monkeypatch.setattr(floor, "read_curves", unsupported)
    q = floor.atomic_fuel.replay_calls(evidence["calls"], lambda read: floor.public_quotes(
        evidence["cap_lamports"], evidence["separate_cents"], 20000, 300, 15000, read))
    assert q.pop("market") is None
    assert q == evidence["result"]
