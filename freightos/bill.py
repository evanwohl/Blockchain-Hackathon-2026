import hashlib
import json
import struct

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey
from solders.system_program import CreateAccountWithSeedParams, create_account_with_seed
from solders.sysvar import INSTRUCTIONS

from freightos.reserve import TOKEN, load_seed

PROGRAM = Pubkey.from_string("AZKLs8psiN3C72oBWGSJyG7hpR23xLCGw8me4YPgnSRt")
RECEIPT_SIZE = 145


def commitment(decision):
    return hashlib.sha256(
        json.dumps(decision, sort_keys=True, separators=(",", ":")).encode()
    ).digest()


def receipt_address(payer, load):
    return Pubkey.create_with_seed(payer, load_seed(load), PROGRAM)


def authorize(payer, vendor, load, amount, cap, digest, creation_rent):
    seed = load_seed(load)
    receipt = receipt_address(payer, load)
    staging = Pubkey.create_with_seed(payer, seed, TOKEN)
    ixs = []
    if creation_rent is not None:
        ixs.append(
            create_account_with_seed(
                CreateAccountWithSeedParams(
                    from_pubkey=payer,
                    to_pubkey=receipt,
                    base=payer,
                    seed=seed,
                    lamports=creation_rent,
                    space=RECEIPT_SIZE,
                    owner=PROGRAM,
                )
            )
        )
    data = b"\x00" + seed.encode() + struct.pack("<QQ", amount, cap) + digest
    ixs.append(
        Instruction(
            PROGRAM,
            data,
            [
                AccountMeta(payer, True, False),
                AccountMeta(receipt, False, True),
                AccountMeta(vendor, False, False),
                AccountMeta(staging, False, False),
            ],
        )
    )
    return ixs


def attest(payer, receipt, digest):
    return Instruction(
        PROGRAM,
        b"\x01" + digest[:16],
        [
            AccountMeta(payer, True, False),
            AccountMeta(receipt, False, True),
            AccountMeta(INSTRUCTIONS, False, False),
        ],
    )


def cancel(payer, receipt):
    return Instruction(
        PROGRAM,
        b"\x02",
        [
            AccountMeta(payer, True, False),
            AccountMeta(receipt, False, True),
        ],
    )
