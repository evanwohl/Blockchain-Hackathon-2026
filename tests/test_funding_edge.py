import pytest

from freightos.app import LoadSettlement, demo_plans, stressed_trial, trial_data
from freightos.verify_receipt import replay
from hedging.invoice_policy import invoice_policy
from hedging.quick_pay import price_calendar


def test_observed_levels_and_pairing_boundary():
    plans, edges, levels, policies = demo_plans()
    assert list(levels) == ["84", "87", "90", "93"]
    assert levels["84"]["recorded_buy_yes"] == "0.23"
    assert levels["90"]["recorded_buy_yes"] is None
    assert plans["quick pay"]["wti-93"]["bill_cents"] == 76000
    edge = edges["quick pay"]["oil stress"]
    assert [edge[k] for k in ("base_cents", "reserve_only_cents", "outside_only_cents", "joint_cents")] == [15000, 15000, 15000, 1345]
    assert edges["cash on hand"]["oil stress"]["boundary"] is None
    for profile, choices in edges.items():
        for key, result in choices.items():
            boundary = result["boundary"]
            if not boundary:
                assert result["pairing_savings_cents"] == 0
                continue
            case = plans[profile][key]["calendar"]
            benchmark = min(result["reserve_only_cents"], result["outside_only_cents"])
            for offset, field in [(0, "cost_at_threshold_cents"), (-1, "cost_one_cent_below_cents")]:
                capacity = boundary["minimum_useful_capacity_cents"] + offset
                offers = [{**s, "net_capacity_cents": capacity} if s["id"] == "reserve redemption" else s
                          for s in case["funding"]]
                cost = price_calendar({**case, "funding": offers})["cost_cents"]
                assert cost == boundary[field]
                assert (cost < benchmark) == (offset == 0)
    assert policies["quick pay"]["peak_reserve_cents"] == 57000
    assert policies["quick pay"]["worst_regret_cents"] == 0


def test_fixed_invoice_choice_pays_positive_regret_when_hindsight_switches():
    case = {"opening_cash_cents": 0, "reserve_floor_cents": 0,
            "invoices": [{"id": "main", "face_cents": 10000, "options": [
                {"name": "standard", "day": 28, "fee_bps": 0}, {"name": "early", "day": 1, "fee_bps": 300}]}],
            "funding": [{"id": "reserve redemption", "available_day": 1, "restore_day": 28,
                         "fixed_cost_cents": 0, "term_cost_bps": 1000, "net_capacity_cents": 10000}]}
    plans = {}
    for value in (500, 8000):
        calendar = {**case, "bills": [{"id": "fuel", "day": 5, "cents": value}]}
        plans[value] = {"calendar": calendar, "cost_cents": price_calendar(calendar)["cost_cents"]}
    assert [p["cost_cents"] for p in plans.values()] == [50, 300]
    policy = invoice_policy(plans)
    assert policy["elections"] == {"main": "early"}
    assert policy["worst_regret_cents"] == 250


def test_market_budget_replays_and_trial_baselines_reconcile():
    app = LoadSettlement()
    app.act("deposit", {})
    review = app.act("review", {"scenario": "wti-93", "profile": "quick pay"})
    receipt = app.act("approve", {"approval": review["id"]})
    assert replay(receipt)["vendor_received_usdc_raw"] == 570_000_000
    assert receipt["decision"]["gemini"]["stress_contract"].endswith("HI93")
    report, _ = trial_data()
    for kind in ("composition", "conventional"):
        values = [x[kind + "_savings_cents"] for x in report["loads"]]
        assert sum(values) == report[kind + "_savings"]["total_cents"]
        assert sum(v > 0 for v in values) == report[kind + "_savings"]["positive_cases"]


def test_guarded_reserve_setup_keeps_wallet_cash_and_can_top_up():
    app = LoadSettlement()
    state = app.state()
    quote = state["setup_cents"]["quick pay"]
    assert quote == 57001 and state["setup_cents"]["cash on hand"] == 0
    assert app.fork.available_after_deposit(quote - 1) < 570_000_000 <= app.fork.available_after_deposit(quote)
    setup = {"version": app.record_version, "profile": "quick pay", "max_cents": quote}
    with pytest.raises(ValueError, match="exceeds"):
        app.act("deposit", {**setup, "max_cents": quote - 1})
    receipt = app.act("deposit", setup)
    assert app.act("deposit", setup) == receipt
    assert app.state()["wallet_usdc_raw"] == 29_990_000
    approval = app.act("review", {"scenario": "wti-93"})
    topup = {**setup, "profile": "credit line", "max_cents": app.state()["setup_cents"]["credit line"]}
    with pytest.raises(ValueError, match="pending"):
        app.act("deposit", topup)
    app.act("cancel", {"approval": approval["id"]})
    app.act("deposit", topup)
    approval = app.act("review", {"scenario": "wti-93", "profile": "credit line"})
    paid = app.act("approve", {"approval": approval["id"]})
    assert replay(paid)["vendor_received_usdc_raw"] == 599_990_000


def test_public_observation_checks_custody_identity_and_uses_one_confirmed_slot():
    import base64
    from freightos.live import recorded_route, reserve_observation
    from freightos.reserve import PROGRAM, TOKEN, USDC
    from solders.pubkey import Pubkey

    route = recorded_route()
    raw = bytearray(165)
    raw[:32] = bytes(USDC)
    raw[32:64] = bytes(Pubkey.from_string(route[4]))
    raw[64:72] = (123456789).to_bytes(8, "little")
    raw[108] = 1

    def reply(method, params):
        if method == "getGenesisHash":
            return "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
        assert method == "getMultipleAccounts" and params[0] == [route[1], route[3]]
        return {"context": {"slot": 77}, "value": [{"owner": str(PROGRAM)}, {"owner": str(TOKEN),
                "data": [base64.b64encode(raw).decode(), "base64"]}]}

    observation = reserve_observation(reply)
    assert observation["slot"] == 77 and observation["balance_usdc_raw"] == "123456789"
    raw[:32] = bytes(32)
    with pytest.raises(ValueError, match="unexpected mint"):
        reserve_observation(reply)


def test_stress_comparison_reprices_both_sides():
    report, _ = trial_data()
    stress = stressed_trial()
    assert stress["conventional"]["savings_cents"] == report["adverse_savings"]["total_cents"]
    same = stress["composition"]
    assert same == {"savings_cents": 55493, "positive_cases": 16, "replaced_cases": 13, "worst_mix_cents": 190}
