import base64

from solders.compute_budget import set_compute_unit_limit
from solders.instruction import Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.transaction_metadata import FailedTransactionMetadata

from freightos import bill
from freightos.fork import ReserveFork, VaultExecutionError, below_withdrawal_minimum
from freightos.reserve import vault_instruction

MEMO = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")


def withdraw(fork, shares):
    return vault_instruction(fork.route, fork.owner, fork.cash, fork.shares, shares)


def instructions(fork, quote):
    return [set_compute_unit_limit(1_400_000), withdraw(fork, quote["return_shares_raw"]),
            Instruction(MEMO, bill.commitment(quote).hex().encode(), [])]


def simulated_cash(fork, withdrawals, *, below_minimum_balance=None):
    result = fork.vm.simulate_transaction(fork.tx([set_compute_unit_limit(1_400_000), *withdrawals]))
    if isinstance(result, FailedTransactionMetadata):
        if below_minimum_balance is not None and below_withdrawal_minimum(result, len(withdrawals)):
            return below_minimum_balance
        raise VaultExecutionError("The reserve could not quote this withdrawal. No money moved.")
    return int.from_bytes(dict(result.post_accounts())[fork.cash].data[64:72], "little")


def return_quote(fork, target_cents, context):
    shares = fork.balance(fork.shares)
    wallet = fork.balance(fork.cash)
    target = target_cents * 10000
    available = fork.redemption_value(shares) if shares else 0
    quote = {**context, "wallet": str(fork.cash), "wallet_before_raw": wallet, "shares_before_raw": shares,
             "protected_cents": target_cents, "return_shares_raw": 0, "return_usdc_raw": 0,
             "remaining_value_raw": available}
    if not available or (target and available < target + 10000):
        return quote
    if target == 0:
        return {**quote, "return_shares_raw": shares, "return_usdc_raw": available, "remaining_value_raw": 0}
    low, high = 0, shares
    while high - low > 1:
        mid = (low + high) // 2
        release = withdraw(fork, mid)
        after = simulated_cash(fork, [release], below_minimum_balance=wallet)
        if after == wallet:
            low = mid
            continue
        # Both withdrawals run against the same starting state and pay vault rounding and fees in order.
        final = simulated_cash(fork, [release, withdraw(fork, shares - mid)], below_minimum_balance=after)
        if final - after >= target:
            low = mid
            quote.update(return_shares_raw=mid, return_usdc_raw=after - wallet, remaining_value_raw=final - after)
        else:
            high = mid
    return quote


def sized_return(fork, quote, extra_cents):
    if type(extra_cents) is not int or extra_cents <= 0:
        raise ValueError("Enter a positive number of cents for the wallet increase")
    wallet = quote["wallet_before_raw"]
    target = (wallet // 10000 + extra_cents) * 10000
    if wallet + quote["return_usdc_raw"] < target:
        raise ValueError("Your wallet and fuel reserve together cannot cover the requested amount")
    low, high = 0, quote["return_shares_raw"]
    while high - low > 1:
        mid = (low + high) // 2
        if simulated_cash(fork, [withdraw(fork, mid)], below_minimum_balance=wallet) >= target:
            high = mid
        else:
            low = mid
    release = withdraw(fork, high)
    after = simulated_cash(fork, [release])
    left = quote["shares_before_raw"] - high
    remaining = simulated_cash(fork, [release, withdraw(fork, left)], below_minimum_balance=after) - after if left else 0
    if after < target or remaining < quote["protected_cents"] * 10000:
        raise ValueError("The reserve cannot cover both the wallet increase and the fuel plan")
    split_cost = max(0, fork.redemption_value(quote["shares_before_raw"]) - (after - wallet) - remaining)
    return {**quote, "extra_wallet_cents": extra_cents, "split_cost_raw": split_cost, "return_shares_raw": high,
            "return_usdc_raw": after - wallet, "remaining_value_raw": remaining}


def return_for_cash(fork, extra_cents, protected_cents=0, context=None):
    quote = return_quote(fork, 0, context or {})
    return sized_return(fork, {**quote, "protected_cents": protected_cents}, extra_cents)


def execute(fork, quote):
    tx = fork.tx(instructions(fork, quote))
    preview = fork.vm.simulate_transaction(tx)
    if isinstance(preview, FailedTransactionMetadata):
        raise VaultExecutionError("Could not confirm the return to your wallet")
    post = dict(preview.post_accounts())
    for key, expected in [(fork.cash, quote["wallet_before_raw"] + quote["return_usdc_raw"]),
                          (fork.shares, quote["shares_before_raw"] - quote["return_shares_raw"])]:
        if int.from_bytes(post[key].data[64:72], "little") != expected:
            raise ValueError("Reserve balances changed. Check the available amount again.")
    result = fork.send(tx)
    return {"kind": "fuel-reserve-return-v1", "payer": str(fork.owner), "vendor_owner": str(fork.vendor_owner),
            "quote": quote, "signature": str(result.signature()), "wallet_after_raw": fork.balance(fork.cash),
            "shares_after_raw": fork.balance(fork.shares), "network_fee_lamports": result.fee(),
            "signed_transactions": list(fork.history)}


def replayed_value(fork, shares):
    if not shares:
        return 0
    probe = Message.new_with_blockhash([set_compute_unit_limit(1_400_000), withdraw(fork, shares)],
                                       fork.owner, fork.vm.latest_blockhash())
    fork.vm.with_sigverify(False)
    try:
        simulation = fork.vm.simulate_transaction(Transaction.new_unsigned(probe))
    finally:
        fork.vm.with_sigverify(True)
    if isinstance(simulation, FailedTransactionMetadata):
        if below_withdrawal_minimum(simulation, 1):
            return 0
        raise ValueError("Could not independently verify the reserve value")
    cash = dict(simulation.post_accounts())[fork.cash]
    return int.from_bytes(cash.data[64:72], "little") - fork.balance(fork.cash)


def replay_return(receipt):
    fork = ReserveFork(Pubkey.from_string(receipt["payer"]), Pubkey.from_string(receipt["vendor_owner"]))
    fork.vm.with_blockhash_check(False)
    quote = receipt["quote"]
    history = receipt["signed_transactions"]
    if len(history) < 2 or quote["wallet"] != str(fork.cash):
        raise ValueError("Incomplete reserve return history")
    full_value = None
    for index, wire in enumerate(history):
        tx = Transaction.from_bytes(base64.b64decode(wire, validate=True))
        tx.verify()
        if tx.message.account_keys[0] != fork.owner:
            raise ValueError("Unexpected return payer")
        if index == len(history) - 1:
            expected = Message.new_with_blockhash(instructions(fork, quote), fork.owner, tx.message.recent_blockhash)
            if tx.message != expected or quote["wallet_before_raw"] != fork.balance(fork.cash) or quote["shares_before_raw"] != fork.balance(fork.shares):
                raise ValueError("Return differs from signed wallet instructions")
            if "extra_wallet_cents" in quote:
                extra = quote["extra_wallet_cents"]
                if type(extra) is not int or extra <= 0 or not 0 < quote["return_shares_raw"] <= quote["shares_before_raw"]:
                    raise ValueError("Invalid targeted return")
                wallet = quote["wallet_before_raw"]
                needed = (wallet // 10000 + extra) * 10000 - wallet
                previous = replayed_value(fork, quote["return_shares_raw"] - 1)
                if not previous < needed <= quote["return_usdc_raw"]:
                    raise ValueError("Return size differs from the requested wallet increase")
                full_value = replayed_value(fork, quote["shares_before_raw"])
        result = fork.send(tx)
    received = fork.balance(fork.cash) - quote["wallet_before_raw"]
    if (received <= 0 or received != quote["return_usdc_raw"] or str(result.signature()) != receipt["signature"]
            or fork.balance(fork.shares) != quote["shares_before_raw"] - quote["return_shares_raw"]
            or fork.balance(fork.shares) != receipt["shares_after_raw"] or fork.balance(fork.cash) != receipt["wallet_after_raw"]
            or receipt["network_fee_lamports"] != result.fee()):
        raise ValueError("Return receipt differs from replayed wallet balances")
    remaining = replayed_value(fork, fork.balance(fork.shares))
    if full_value is not None and quote["split_cost_raw"] != max(0, full_value - received - remaining):
        raise ValueError("Split cost differs from the replayed vault values")
    if remaining != quote["remaining_value_raw"] or remaining < quote["protected_cents"] * 10000:
        raise ValueError("Protected reserve differs from replay")
    return {"verified_signatures": len(history), "wallet_usdc_raw": fork.balance(fork.cash),
            "returned_usdc_raw": received, "remaining_shares_raw": fork.balance(fork.shares),
            "protected_value_usdc_raw": remaining}
