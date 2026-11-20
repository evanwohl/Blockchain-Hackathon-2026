"""Size a recurring fuel loan against a hindsight borrower with the same account rules."""
import numpy as np

from freightos import mainnet_loan as lending

IMPOSSIBLE = -(1 << 55)


def transitions(oracle, cash):
    return tuple(np.max(values[:, None] - cash, axis=0) for values in oracle)


def worst_gap(before, first, later, opening, booked, stops):
    fresh, existing, worst = 0, IMPOSSIBLE, IMPOSSIBLE
    for stop in range(1, stops + 1):
        stay, start, reuse = before if opening is None or stop < opening else first if stop == opening else later
        existing = np.maximum(fresh + start, existing + reuse)
        fresh = fresh + stay
        if stop >= booked:
            worst = np.maximum(worst, np.maximum(fresh, existing))
    return worst


def witness(oracle, cash, booked, prices):
    states = {False: (0, [], None, 0)}
    worst = -1
    for stop, own in enumerate(cash, 1):
        next_states = {}
        for active, (loss, path, start, proceeds) in states.items():
            for next_active, edge in ([(True, 2)] if active else [(False, 0), (True, 1)]):
                delta = oracle[edge] - own
                idx = int(delta.argmax())
                if oracle[edge][idx] == IMPOSSIBLE:
                    continue
                value = loss + int(delta[idx])
                if next_active not in next_states or value > next_states[next_active][0]:
                    opening = stop if next_active and not active else start
                    next_states[next_active] = value, path + [prices[idx]], opening, proceeds + int(own[idx])
        states = next_states
        for value, path, start, proceeds in states.values():
            if stop >= booked and value > worst:
                worst = value
                result = dict(stops=stop, prices=path, rival_stop=start, cash_cents=proceeds)
    return worst, result


def size(series, plan, prices, schedule, costs, cash, stable, cap, fee, rent, setup):
    booking = series["booking"]
    old = booking["choice"]
    if old is None:
        return
    booked, stops = booking["booked"], booking["stops"]
    prices = sorted(set(prices))
    high = prices[-1]
    floor = plan["floor_cents"]
    first, later, outside = (series[key] for key in ("first", "later", "debt_free"))
    template = first if old["open_stop"] == 1 else outside
    net, discount = template["advance_cents"], template["fee_cents"]
    gap = template["sale_cents"] + template["draw_cents"]

    def row(draw, returning):
        spend = 2 * lending.LOAN_FEE_ALLOWANCE + setup["setup_fee_lamports"]
        if not returning:
            spend += setup["retained_lamports"]
        return lending.funding_row(plan, net, discount, cash, stable, draw, schedule[draw], costs,
                                   fee, rent, spend, setup["refunded_lamports"])

    def balances(r):
        return np.array([lending.payday_at(r, high, p) for p in prices], dtype=np.int64)

    def fits(r):
        return r is not None and r["payday_cents"] >= floor and r["opening_lamports"] <= cap

    # Each column is one whole-cent loan. An infeasible first loan can still fit after setup.
    draws = np.array([d for d in range(max(1, gap - len(costs) + 1), min(gap + 1, len(schedule)))
                      if schedule[d] is not None], dtype=np.int64)
    price_array = np.array(prices, dtype=np.int64)[:, None]
    curves, valid = [], []
    for returning in (False, True):
        high_cash, restore, feasible = [], [], []
        for d in draws:
            r = row(int(d), returning)
            high_cash.append(r["payday_cents"])
            restore.append(r["restore_lamports"])
            feasible.append(fits(r))
        sols = np.asarray(restore, dtype=np.int64)
        base = np.asarray(high_cash, dtype=np.int64) - (-sols * high // lending.LAMPORTS)
        curves.append(base + (-sols * price_array // lending.LAMPORTS))
        valid.append(np.asarray(feasible, dtype=bool))

    zero = row(0, False) if gap < len(costs) else None
    outside_rows = [r for r in (outside, zero) if fits(r)]
    unavailable = np.full(len(prices), IMPOSSIBLE, dtype=np.int64)
    def envelope(rows):
        return np.maximum.reduce([balances(r) for r in rows]) if rows else unavailable.copy()

    new = envelope([first] if fits(first) else [])
    reused = envelope(outside_rows + ([later] if fits(later) else []))
    for dest, curve, ok in zip((new, reused), curves, valid):
        if ok.any():
            np.maximum(dest, np.max(curve, where=ok, initial=IMPOSSIBLE, axis=1), out=dest)
    oracle = (envelope(outside_rows), new, reused)

    def gaps(r):
        return transitions(oracle, balances(r)[:, None]) if fits(r) else None

    candidates = {}
    def record(opening, loss, first_row, later_row, outside_row):
        total_debt = 0 if opening is None else first_row["draw_cents"] + (stops - opening) * (later_row["draw_cents"] if later_row else 0)
        rank = int(loss), total_debt, -(opening or stops + 1)
        if opening not in candidates or rank < candidates[opening][0]:
            candidates[opening] = rank, first_row, later_row, outside_row

    initial, repeat, debt_free = gaps(first), gaps(later), gaps(outside)
    for a in booking["alternatives"]:
        opening = a["open_stop"]
        loss = worst_gap(debt_free, initial, repeat, opening, booked, stops).item()
        record(opening, loss, first, later, outside)
    baseline = candidates[old["open_stop"]][0][0]

    initial, repeat = (transitions(oracle, matrix) for matrix in curves)
    for before in outside_rows or [None]:
        free = gaps(before)
        if free is not None:
            record(None, worst_gap(free, None, None, None, booked, stops).item(), None, None, before)
        for opening in range(1, stops + 1):
            ok = valid[0] if opening == stops else valid[0] & valid[1]
            if not ok.any() or (opening > 1 and free is None):
                continue
            losses = worst_gap(free, initial, repeat, opening, booked, stops)
            idx = int(np.where(ok, losses, -IMPOSSIBLE).argmin())
            d = int(draws[idx])
            record(opening, losses[idx], row(d, False), row(d, True), before)

    opening = min(candidates, key=lambda d: candidates[d][0])
    rank, first, later, outside = candidates[opening]
    stages = [outside if opening is None or i < opening else first if i == opening else later for i in range(1, stops + 1)]
    checked, case = witness(oracle, [balances(r) for r in stages], booked, prices)
    if checked != rank[0]:
        raise ArithmeticError("Loan sizing witness disagrees with the selected route")
    alternatives = [dict(open_stop=d, missed_cents=v[0][0], draw_cents=v[1]["draw_cents"] if d else 0)
                    for d, v in sorted(candidates.items(), key=lambda pair: pair[0] or 0)]
    booking.update(choice=dict(open_stop=opening, missed_cents=checked, witness=case), alternatives=alternatives)
    series.update(first=first, later=later, debt_free=outside, prices=[], renewal_pct=None,
                  break_even_stops=None, open_cents=None, debt_free_cents=None,
                  sizing=dict(baseline_cents=baseline, baseline_stop=old["open_stop"], advance_cents=net,
                              discount_cents=discount, draw_count=int(valid[0].sum())))
