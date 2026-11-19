import hashlib
import struct

from solders.compute_budget import set_compute_unit_limit
from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey
from solders.system_program import CreateAccountWithSeedParams, create_account_with_seed

from freightos.capture import VAULT_PROGRAM

TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
USDC = Pubkey.from_string("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
PROGRAM = Pubkey.from_string(VAULT_PROGRAM)
VAULT = Pubkey.from_string("HDsayqAsDWy3QvANGqh2yNraqcD8Fnjgh73Mhb3WRS5E")
ATA = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")


def associated(owner, mint):
    return Pubkey.find_program_address([bytes(owner), bytes(TOKEN), bytes(mint)], ATA)[
        0
    ]


def vault_instruction(route, owner, token_account, shares, amount, *, deposit=False, venue=(PROGRAM, VAULT, USDC)):
    keys = [Pubkey.from_string(s) for s in route]
    program, vault, usdc = venue
    if keys[1] != vault or keys[6] != usdc or keys[13] != program:
        raise ValueError("Unsupported vault")
    keys[0], keys[5], keys[7] = owner, token_account, shares
    if deposit:
        keys = [keys[i] for i in (0, 1, 3, 6, 4, 8, 5, 7, 11, 9, 10, 12, 13)] + keys[
            14:
        ]
        name = "deposit"
        writable = {0, 1, 2, 5, 6, 7}
        offset = 13
    else:
        name = "withdraw_from_available"
        writable = {1, 3, 5, 6, 7, 8}
        offset = 14
    reserves, rem = divmod(len(keys) - offset, 2)
    if rem or not 0 < amount < 2**64:
        raise ValueError("Unsupported reserve list or amount")
    writable.update(range(offset, offset + reserves))
    metas = [AccountMeta(key, i == 0, i in writable) for i, key in enumerate(keys)]
    discriminator = hashlib.sha256(("global:" + name).encode()).digest()[:8]
    return Instruction(program, discriminator + struct.pack("<Q", amount), metas)


def load_seed(load_id):
    return hashlib.sha256(b"FreightOS/load-payment/v1/" + load_id.encode()).hexdigest()[
        :32
    ]


def payment_instructions(
    route, owner, recipient_usdc, load_id, exact_usdc, max_shares, rent, *, venue=(PROGRAM, VAULT, USDC)
):
    usdc = venue[2]
    if not load_id or not 0 < exact_usdc < 2**64:
        raise ValueError("A load ID and positive USDC amount are required")
    seed = load_seed(load_id)
    staging = Pubkey.create_with_seed(owner, seed, TOKEN)
    mint = Pubkey.from_string(route[8])
    if recipient_usdc in (staging, associated(owner, usdc)):
        raise ValueError("Expected a distinct payee token account")
    create = create_account_with_seed(
        CreateAccountWithSeedParams(
            from_pubkey=owner,
            to_pubkey=staging,
            base=owner,
            seed=seed,
            lamports=rent,
            space=165,
            owner=TOKEN,
        )
    )
    initialize = Instruction(
        TOKEN,
        b"\x12" + bytes(owner),
        [AccountMeta(staging, False, True), AccountMeta(usdc, False, False)],
    )
    redeem = vault_instruction(
        route, owner, staging, associated(owner, mint), max_shares, venue=venue
    )
    pay = Instruction(
        TOKEN,
        b"\x0c" + struct.pack("<QB", exact_usdc, 6),
        [
            AccountMeta(staging, False, True),
            AccountMeta(usdc, False, False),
            AccountMeta(recipient_usdc, False, True),
            AccountMeta(owner, True, False),
        ],
    )
    return staging, [set_compute_unit_limit(1_000_000), create, initialize, redeem, pay]
