"""Prepare a wallet-signable payment from liquid Kamino vault shares using public RPC."""

import argparse
import base64
import gzip
import hashlib
import json
from decimal import Decimal

from solders.hash import Hash
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

from freightos.capture import FIXTURE, rpc
from freightos.reserve import TOKEN, USDC, associated, payment_instructions


def raw_units(value):
    scaled = Decimal(value) * 1_000_000
    if (
        not scaled.is_finite()
        or not 0 < scaled < 2**64
        or scaled != scaled.to_integral_value()
    ):
        raise ValueError("Expected a positive amount with at most six decimal places")
    return int(scaled)


def token_amount(account, mint, owner):
    if not account or account["owner"] != str(TOKEN):
        raise ValueError("Missing SPL token account")
    data = base64.b64decode(account["data"][0], validate=True)
    if (
        len(data) != 165
        or data[:32] != bytes(mint)
        or data[32:64] != bytes(owner)
        or data[108] != 1
    ):
        raise ValueError("Token account has an unexpected mint, owner or state")
    return int.from_bytes(data[64:72], "little")


def prepare(payer, payee, load, amount, max_shares, request=rpc):
    if payer == payee:
        raise ValueError("Vendor and payer must differ")
    if request("getGenesisHash", []) != "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d":
        raise ValueError("Expected Solana mainnet")
    fixture = (FIXTURE / "accounts.json.gz").read_bytes()
    if (
        hashlib.sha256(fixture).hexdigest()
        != json.loads((FIXTURE / "manifest.json").read_text())["sha256"]
    ):
        raise ValueError("Unexpected route definition")
    route = json.loads(gzip.decompress(fixture))["withdraw_accounts"]
    mint = Pubkey.from_string(route[8])
    destination = associated(payee, USDC)
    share_account = associated(payer, mint)
    rent = request(
        "getMinimumBalanceForRentExemption", [165, {"commitment": "confirmed"}]
    )
    staging, instructions = payment_instructions(
        route, payer, destination, load, amount, max_shares, rent
    )
    keys = [destination, share_account, staging]
    accounts = request(
        "getMultipleAccounts",
        [[str(k) for k in keys], {"encoding": "base64", "commitment": "confirmed"}],
    )
    vendor, shares, existing = accounts["value"]
    token_amount(vendor, USDC, payee)
    if token_amount(shares, mint, payer) < max_shares:
        raise ValueError("Insufficient unstaked vault shares")
    if existing is not None:
        raise ValueError("This load already has a settlement account")
    config = {"commitment": "confirmed", "minContextSlot": accounts["context"]["slot"]}
    recent = request("getLatestBlockhash", [config])["value"]
    message = Message.new_with_blockhash(
        instructions, payer, Hash.from_string(recent["blockhash"])
    )
    wire = base64.b64encode(bytes(Transaction.new_unsigned(message))).decode()
    preview = request(
        "simulateTransaction",
        [
            wire,
            {
                **config,
                "sigVerify": False,
                "encoding": "base64",
                "accounts": {"encoding": "base64", "addresses": [str(k) for k in keys]},
            },
        ],
    )
    result = preview["value"]
    if result["err"] is not None:
        raise ValueError(f"Payment simulation failed: {result['err']}")
    payee_post, share_post, staging_post = result["accounts"]
    remaining = token_amount(staging_post, USDC, payer)
    token_amount(payee_post, USDC, payee)
    token_amount(share_post, mint, payer)
    fee = request(
        "getFeeForMessage", [base64.b64encode(bytes(message)).decode(), config]
    )["value"]
    if fee is None:
        raise ValueError("Quote expired")
    return {
        "cluster": "mainnet-beta",
        "status": "simulated_unsigned",
        "load": load,
        "payer": str(payer),
        "payee": str(payee),
        "payee_usdc_account": str(destination),
        "load_account": str(staging),
        "payment_usdc_raw": amount,
        "maximum_shares_raw": max_shares,
        "simulated_excess_usdc_raw": remaining,
        "fee_lamports": fee,
        "rent_lamports": rent,
        "simulation_slot": preview["context"]["slot"],
        "last_valid_block_height": recent["lastValidBlockHeight"],
        "transaction_base64": wire,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("payer", "payee", "load", "usdc", "max-shares"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                Pubkey.from_string(args.payer),
                Pubkey.from_string(args.payee),
                args.load,
                raw_units(args.usdc),
                raw_units(args.max_shares),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
