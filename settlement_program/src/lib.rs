#![allow(unexpected_cfgs)]

use solana_program::{
    account_info::AccountInfo,
    entrypoint,
    entrypoint::ProgramResult,
    instruction::{get_stack_height, Instruction, TRANSACTION_LEVEL_STACK_HEIGHT},
    program_error::ProgramError,
    pubkey,
    pubkey::Pubkey,
    sysvar::instructions::{load_current_index_checked, load_instruction_at_checked},
};

entrypoint!(process);

const TOKEN: Pubkey = pubkey!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
const USDC: Pubkey = pubkey!("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v");
const KAMINO: Pubkey = pubkey!("KvauGMspG5k6rtzrqqn7WNn3oZdyKqLKwK2XWQ8FLjd");
const VAULT: Pubkey = pubkey!("HDsayqAsDWy3QvANGqh2yNraqcD8Fnjgh73Mhb3WRS5E");
const WITHDRAW: [u8; 8] = [19, 131, 112, 155, 170, 220, 34, 57];
const SIZE: usize = 1 + 32 * 4 + 8 * 2;
const AUTHORIZED: u8 = 1;
const SETTLED: u8 = 2;

fn require(ok: bool, error: u32) -> ProgramResult {
    if ok {
        Ok(())
    } else {
        Err(ProgramError::Custom(error))
    }
}

fn account_is(ix: &Instruction, index: usize, key: &[u8]) -> bool {
    ix.accounts
        .get(index)
        .is_some_and(|a| a.pubkey.as_ref() == key)
}

fn amount(data: &[u8]) -> u64 {
    u64::from_le_bytes(data.try_into().unwrap())
}

fn process(id: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> ProgramResult {
    let [payer, receipt, rest @ ..] = accounts else {
        return Err(ProgramError::NotEnoughAccountKeys);
    };
    require(
        payer.is_signer && receipt.is_writable && receipt.owner == id,
        1,
    )?;
    require(get_stack_height() == TRANSACTION_LEVEL_STACK_HEIGHT, 2)?;
    let mut state = receipt.try_borrow_mut_data()?;
    require(state.len() == SIZE && state[0] != SETTLED, 3)?;
    if state[0] != 0 {
        require(&state[1..33] == payer.key.as_ref(), 1)?;
    }
    match data.first() {
        Some(0) => {
            let [vendor, staging] = rest else {
                return Err(ProgramError::NotEnoughAccountKeys);
            };
            require(data.len() == 81, 4)?;
            let seed = std::str::from_utf8(&data[1..33]).map_err(|_| ProgramError::InvalidSeeds)?;
            require(
                Pubkey::create_with_seed(payer.key, seed, id)? == *receipt.key,
                5,
            )?;
            require(
                Pubkey::create_with_seed(payer.key, seed, &TOKEN)? == *staging.key,
                5,
            )?;
            let vendor_data = vendor.try_borrow_data()?;
            require(
                vendor.owner == &TOKEN
                    && vendor_data.len() == 165
                    && &vendor_data[..32] == USDC.as_ref()
                    && vendor_data[108] == 1
                    && &vendor_data[32..64] != payer.key.as_ref()
                    && vendor.key != staging.key,
                6,
            )?;
            require(amount(&data[33..41]) > 0 && amount(&data[41..49]) > 0, 4)?;
            state[0] = AUTHORIZED;
            state[1..33].copy_from_slice(payer.key.as_ref());
            state[33..65].copy_from_slice(vendor.key.as_ref());
            state[65..97].copy_from_slice(staging.key.as_ref());
            state[97..113].copy_from_slice(&data[33..49]);
            state[113..145].copy_from_slice(&data[49..81]);
        }
        Some(1) => {
            let [instructions] = rest else {
                return Err(ProgramError::NotEnoughAccountKeys);
            };
            require(state[0] == AUTHORIZED && data[1..] == state[113..129], 7)?;
            let current = usize::from(load_current_index_checked(instructions)?);
            require(current >= 3, 8)?;
            let initialize = load_instruction_at_checked(current - 3, instructions)?;
            require(
                initialize.program_id == TOKEN
                    && initialize.data.len() == 33
                    && initialize.data[0] == 18
                    && &initialize.data[1..] == payer.key.as_ref()
                    && account_is(&initialize, 0, &state[65..97])
                    && account_is(&initialize, 1, USDC.as_ref()),
                8,
            )?;
            let redeem = load_instruction_at_checked(current - 2, instructions)?;
            let pay = load_instruction_at_checked(current - 1, instructions)?;
            require(
                redeem.program_id == KAMINO
                    && redeem.data.len() == 16
                    && redeem.data[..8] == WITHDRAW
                    && amount(&redeem.data[8..]) > 0
                    && amount(&redeem.data[8..]) <= amount(&state[105..113])
                    && account_is(&redeem, 0, payer.key.as_ref())
                    && account_is(&redeem, 1, VAULT.as_ref())
                    && account_is(&redeem, 5, &state[65..97])
                    && account_is(&redeem, 6, USDC.as_ref()),
                9,
            )?;
            require(
                pay.program_id == TOKEN
                    && pay.data.len() == 10
                    && pay.data[0] == 12
                    && pay.data[9] == 6
                    && pay.data[1..9] == state[97..105]
                    && account_is(&pay, 0, &state[65..97])
                    && account_is(&pay, 1, USDC.as_ref())
                    && account_is(&pay, 2, &state[33..65])
                    && account_is(&pay, 3, payer.key.as_ref()),
                10,
            )?;
            state[0] = SETTLED;
        }
        Some(2) if data.len() == 1 && rest.is_empty() => {
            require(state[0] == AUTHORIZED, 7)?;
            state[0] = 0;
        }
        _ => return Err(ProgramError::InvalidInstructionData),
    }
    Ok(())
}
