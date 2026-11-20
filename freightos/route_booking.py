"""Choose when to open a loan when the length of a fuel run is uncertain."""
from freightos.mainnet_loan import payday_at


def compare(first, repeat, outside, booked, stops, prices, floor):
    prices = sorted(set(prices))
    rows = {"first": first, "repeat": repeat, "outside": outside}
    cash = {name: {p: payday_at(row, prices[-1], p) for p in prices}
            for name, row in rows.items() if row is not None and row["payday_cents"] >= floor}
    schedules = {None: ["outside"] * stops} if "outside" in cash else {}
    for opening in range(1, stops + 1):
        stages = ["outside"] * (opening - 1) + ["first"] + ["repeat"] * (stops - opening)
        if all(stage in cash for stage in stages):
            schedules[opening] = stages

    # Restoring balances between trips lets each stop attain its worst pairwise gap independently.
    gaps = {}
    for name, left in cash.items():
        for rival, right in cash.items():
            price = max(prices, key=lambda p: right[p] - left[p])
            gaps[name, rival] = right[price] - left[price], price

    alternatives = []
    for opening, stages in schedules.items():
        worst = -1
        for rival, other in schedules.items():
            loss = proceeds = 0
            path = []
            for i, (own_stage, rival_stage) in enumerate(zip(stages, other), 1):
                difference, price = gaps[own_stage, rival_stage]
                loss += difference
                proceeds += cash[own_stage][price]
                path.append(price)
                if i >= booked and loss > worst:
                    worst = loss
                    witness = dict(stops=i, prices=path.copy(), rival_stop=rival, cash_cents=proceeds)
        alternatives.append(dict(open_stop=opening, missed_cents=worst, witness=witness))
    best = min(alternatives, key=lambda a: (a["missed_cents"], -(a["open_stop"] or stops + 1)), default=None)
    return dict(booked=booked, stops=stops, price_count=len(prices), choice=best, alternatives=alternatives)
