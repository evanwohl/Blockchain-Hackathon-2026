import base64

from solders.compute_budget import set_compute_unit_limit
from solders.instruction import Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.transaction_metadata import FailedTransactionMetadata

from freightos import bill
from freightos.fork import ReserveFork, VaultExecutionError
from freightos.reserve import vault_instruction

MEMO = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")


def withdraw(fork, shares):
    return vault_instruction(fork.route, fork.owner, fork.cash, fork.shares, shares)


def instructions(fork, quote):
    return [set_compute_unit_limit(1_400_000), withdraw(fork, quote["return_shares_raw"]),
            Instruction(MEMO, bill.commitment(quote).hex().encode(), [])]


def simulated_cash(fork, withdrawals):
    result = fork.vm.simulate_transaction(fork.tx([set_compute_unit_limit(1_400_000), *withdrawals]))
    if isinstance(result, FailedTransactionMetadata):
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
        after = simulated_cash(fork, [release])
        # Both withdrawals run against the same starting state and pay vault rounding and fees in order.
        final = simulated_cash(fork, [release, withdraw(fork, shares - mid)])
        if final - after >= target:
            low = mid
            quote.update(return_shares_raw=mid, return_usdc_raw=after - wallet, remaining_value_raw=final - after)
        else:
            high = mid
    return quote


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


def replay_return(receipt):
    fork = ReserveFork(Pubkey.from_string(receipt["payer"]), Pubkey.from_string(receipt["vendor_owner"]))
    fork.vm.with_blockhash_check(False)
    quote = receipt["quote"]
    history = receipt["signed_transactions"]
    if len(history) < 2 or quote["wallet"] != str(fork.cash):
        raise ValueError("Incomplete reserve return history")
    for index, wire in enumerate(history):
        tx = Transaction.from_bytes(base64.b64decode(wire, validate=True))
        tx.verify()
        if tx.message.account_keys[0] != fork.owner:
            raise ValueError("Unexpected return payer")
        if index == len(history) - 1:
            expected = Message.new_with_blockhash(instructions(fork, quote), fork.owner, tx.message.recent_blockhash)
            if tx.message != expected or quote["wallet_before_raw"] != fork.balance(fork.cash) or quote["shares_before_raw"] != fork.balance(fork.shares):
                raise ValueError("Return differs from signed wallet instructions")
        result = fork.send(tx)
    received = fork.balance(fork.cash) - quote["wallet_before_raw"]
    if (received <= 0 or received != quote["return_usdc_raw"] or str(result.signature()) != receipt["signature"]
            or fork.balance(fork.shares) != quote["shares_before_raw"] - quote["return_shares_raw"]
            or fork.balance(fork.shares) != receipt["shares_after_raw"] or fork.balance(fork.cash) != receipt["wallet_after_raw"]
            or receipt["network_fee_lamports"] != result.fee()):
        raise ValueError("Return receipt differs from replayed wallet balances")
    remaining = 0
    if fork.balance(fork.shares):
        probe = Message.new_with_blockhash([set_compute_unit_limit(1_400_000), withdraw(fork, fork.balance(fork.shares))],
                                          fork.owner, fork.vm.latest_blockhash())
        # All recorded signatures were verified above. This final read-only probe has no payer key.
        fork.vm.with_sigverify(False)
        simulation = fork.vm.simulate_transaction(Transaction.new_unsigned(probe))
        if isinstance(simulation, FailedTransactionMetadata):
            raise ValueError("Could not independently verify the protected reserve")
        cash_account = dict(simulation.post_accounts())[fork.cash]
        remaining = int.from_bytes(cash_account.data[64:72], "little") - fork.balance(fork.cash)
    if remaining != quote["remaining_value_raw"] or remaining < quote["protected_cents"] * 10000:
        raise ValueError("Protected reserve differs from replay")
    return {"verified_signatures": len(history), "wallet_usdc_raw": fork.balance(fork.cash),
            "returned_usdc_raw": received, "remaining_shares_raw": fork.balance(fork.shares),
            "protected_value_usdc_raw": remaining}
