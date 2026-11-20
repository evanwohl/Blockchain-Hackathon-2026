"""Reconcile the demo customer's payment and the carrier's Kamino withdrawal on Devnet."""

import argparse
import hashlib
import json
import struct
from functools import lru_cache

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

from freightos import devnet
from freightos.capture import ROOT
from freightos.reserve import TOKEN, associated
from freightos.surcharge import offer_decision, surcharge_plans

SAMPLE = devnet.SAMPLE.with_name("offer.json")
SETTLEMENT_SIGNATURE = "2MAefNg3M2xFj27U82XEg2bwBQDhnmSUD24Ta4s7o47nN4hu5T1dMTKwyhAtHRZR9TQpFa2rAVa1yrNPx1QTGzvc"


@lru_cache(maxsize=1)
def price_offer():
    plans, policy, offer = offer_decision(18000, 5, 28, 500, 59999, "oil stress")
    plan = plans["oil stress"]
    for path, digest in plan["input_sha256"].items():
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest:
            raise ValueError("The fuel or reserve evidence has changed")
    return plan, policy, offer


def fee_breakdown(plan):
    faces = {i["id"]: i["face_cents"] for i in plan["calendar"]["invoices"]}
    discounts = {row["invoice"]: faces[row["invoice"]] - row["receipt_cents"] for row in plan["invoice_elections"]}
    costs = {"customer_fee_cents": discounts.pop("fuel surcharge"),
             "invoice_fees_cents": sum(discounts.values()),
             "reserve_cost_cents": sum(row["cost_cents"] for row in plan["funding"])}
    if sum(costs.values()) != plan["cost_cents"]:
        raise ValueError("The funding costs do not add up")
    return costs


@lru_cache(maxsize=1)
def compare_costs():
    plan, policy, offer = price_offer()
    terms = plan["surcharge"]
    cents, day, regular = terms["cents"], terms["day"], terms["regular_day"]
    capacity = terms["capacity_cents"]
    planned = policy["peak_reserve_cents"]
    waiting = surcharge_plans(cents, regular, capacity)[0][plan["scenario"]]
    _, _, limited = offer_decision(cents, day, regular, terms["fee_cents"], planned, plan["scenario"])
    if (not limited["take_offer"] or limited["early_cost_cents"] != plan["cost_cents"]
            or limited["wait_cost_cents"] != waiting["cost_cents"]):
        raise ValueError("The reserve budgets do not reproduce the published cost comparison")
    return {"early": fee_breakdown(plan), "wait": fee_breakdown(waiting),
            "wait_draw_cents": waiting["payment_cents"], "early_draw_cents": plan["payment_cents"],
            "planned_cents": planned, "planned_fee_ceiling_cents": limited["highest_fee_cents"],
            "available_cents": capacity, "available_fee_ceiling_cents": offer["highest_fee_cents"]}


def decision_for(customer, vendor):
    plan, policy, offer = price_offer()
    if not offer["take_offer"]:
        raise ValueError("The early offer no longer meets the fuel budgets")
    return {"cluster": "devnet", "load": plan["load"] + "-OFFER", "source_load": plan["load"],
            "scenario": plan["scenario"], "profile": plan["profile"], "surcharge": plan["surcharge"],
            "bill_cents": plan["bill_cents"], "reserve_cents": plan["payment_cents"],
            "outside_cents": plan["other_funds_cents"], "fees_cents": plan["cost_cents"],
            "wait_cost_cents": offer["wait_cost_cents"], "highest_fee_cents": offer["highest_fee_cents"],
            "least_saved_cents": offer["least_saved_cents"], "budgets": len(policy["outcomes"]),
            "peak_reserve_cents": policy["peak_reserve_cents"], "input_sha256": plan["input_sha256"],
            "customer": str(customer), "vendor": str(associated(vendor, devnet.MINT)),
            "mint": str(devnet.MINT), "raw_units_per_cent": 100}


def transfer(source, owner, destination, raw):
    return Instruction(TOKEN, b"\x0c" + struct.pack("<QB", raw, 6), [
        AccountMeta(source, False, True), AccountMeta(devnet.MINT, False, False),
        AccountMeta(destination, False, True), AccountMeta(owner, True, False)])


class OfferPayment(devnet.PublicPayment):
    def __init__(self, run):
        super().__init__(run, Pubkey.from_string(run["payer"]))
        decision = run["decision"]
        self.customer = Pubkey.from_string(decision["customer"])
        self.customer_cash = associated(self.customer, devnet.MINT)
        self.custody, self.vault_authority = map(Pubkey.from_string, self.route[3:5])
        if len({self.payer, self.payee, self.customer}) != 3 or decision != decision_for(self.customer, self.payee):
            raise ValueError("The public terms differ from the priced offer")
        scale = decision["raw_units_per_cent"]
        self.gross = decision["surcharge"]["cents"] * scale
        self.customer_part = decision["outside_cents"] * scale
        if self.gross - self.customer_part != decision["surcharge"]["fee_cents"] * scale:
            raise ValueError("Customer proceeds differ from the agreed fee")
        self.prepare = [*self.deposit, devnet.create_ata(self.payer, self.customer, devnet.MINT),
                        transfer(self.cash, self.payer, self.customer_cash, self.gross), *self.approval]
        self.settle = [self.payment[0], transfer(self.customer_cash, self.customer, self.cash, self.customer_part),
                       transfer(self.cash, self.payer, self.destination, self.customer_part), *self.payment[1:]]


def verify(run, rpc=None):
    if run["transactions"]["settle"]["signature"] != SETTLEMENT_SIGNATURE:
        raise ValueError("Unexpected public offer settlement")
    payment = OfferPayment(run)
    if rpc and rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("Expected Solana Devnet")
    steps = {}
    for step in ("prepare", "settle"):
        signature = run["transactions"][step]["signature"]
        data = rpc("getTransaction", [signature, {"encoding": "base64", "commitment": "finalized",
                                                  "maxSupportedTransactionVersion": 0}]) if rpc else run["transactions"][step]
        keys = devnet.check_transaction(data, signature, getattr(payment, step), payment.payer)
        meta = data["meta"]
        vault_delta = devnet.token_delta(data, keys, payment.custody, devnet.MINT, payment.vault_authority)
        if step == "prepare":
            carried, prepared = devnet.token_balances(meta, keys, payment.shares, devnet.SHARES, payment.payer)
            minted = prepared - carried
            seeded = devnet.token_delta(data, keys, payment.customer_cash, devnet.MINT, payment.customer)
            spent = devnet.token_delta(data, keys, payment.cash, devnet.MINT, payment.payer)
            if (minted < payment.cap or seeded != payment.gross or spent != -seeded - run["deposit_raw"]
                    or vault_delta != run["deposit_raw"]):
                raise ValueError("Reserve deposit or customer funding differs")
        else:
            opened, closed = devnet.token_balances(meta, keys, payment.shares, devnet.SHARES, payment.payer)
            burned = opened - closed
            received = devnet.token_delta(data, keys, payment.destination, devnet.MINT, payment.payee)
            before, after = devnet.token_balances(meta, keys, payment.customer_cash, devnet.MINT, payment.customer)
            reserve_paid = -vault_delta
            staging_delta = devnet.token_delta(data, keys, payment.staging, devnet.MINT, payment.payer)
            cash_before, cash_after = devnet.token_balances(meta, keys, payment.cash, devnet.MINT, payment.payer)
            if (opened != prepared or not 0 < burned <= payment.cap or before != payment.gross or before - after != payment.customer_part
                    or cash_before != cash_after or staging_delta != 0 or reserve_paid != payment.amount
                    or received != reserve_paid + payment.customer_part):
                raise ValueError("The two contributions do not reconcile to the fuel bill")
        steps[step] = {"signature": signature, "slot": data["slot"]}
    slot = steps["settle"]["slot"]
    if steps["prepare"]["slot"] > slot:
        raise ValueError("The payment predates its approval")
    receipt = rpc("getAccountInfo", [str(payment.receipt), {"encoding": "base64", "commitment": "finalized",
                                                            "minContextSlot": slot}]) if rpc else run["receipt"]
    if receipt["context"]["slot"] < slot:
        raise ValueError("The public receipt predates settlement")
    payment.check_receipt(receipt["value"], 2)
    return {"decision": run["decision"], "cost_comparison": compare_costs(), "received_raw": received, "customer_paid_raw": before - after,
            "customer_kept_raw": after, "reserve_paid_raw": reserve_paid, "shares_burned_raw": burned,
            "wallet_after_settlement_raw": cash_after, "shares_before_setup_raw": carried, "shares_minted_raw": minted, "shares_after_raw": closed,
            "share_account": str(payment.shares), "receipt": str(payment.receipt), "steps": steps, "source": "public-rpc" if rpc else "saved-receipt"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Fetch both finalized transactions from public Devnet")
    args = parser.parse_args()
    print(json.dumps(verify(json.loads(SAMPLE.read_bytes()), devnet.request if args.live else None), indent=2))


if __name__ == "__main__":
    main()
