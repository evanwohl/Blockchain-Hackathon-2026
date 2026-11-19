# FreightOS

## Public payment demo

Open **See a full bill paid on Solana** on Overview. For a $745 sample fuel bill the demo customer pays 1.75 test USDC through the driver, and Kamino supplies 5.70 test USDC. The customer and driver signed the same transaction. The vendor received all 7.45 test USDC on public devnet. Each test cent represents one modeled dollar. [View the finalized settlement on Solana Explorer](https://explorer.solana.com/tx/2MAefNg3M2xFj27U82XEg2bwBQDhnmSUD24Ta4s7o47nN4hu5T1dMTKwyhAtHRZR9TQpFa2rAVa1yrNPx1QTGzvc?cluster=devnet)

Run `uv run --locked python -m freightos.public_offer --live` to verify the offer terms, both signatures, vault withdrawal and vendor balance. The original reserve-only example is available at `/public-payment` and through `python -m freightos.devnet verify`. See [the project guide](AGENTS.md) for the demo flow and execution details.

FreightOS compares ways to fund a load’s fuel bill, shows how a recorded Gemini oil scenario changes the cash needed, and pays the approved vendor from a Kamino USDC reserve. The browser app uses test funds on a local Solana fork. It runs offline after dependency installation.

## Run the app

From the repository root with Python 3.11 or later and uv installed, run:

```bash
uv sync --locked
uv run --locked python -m freightos.app
```

Open [the local app](http://127.0.0.1:8783). Choose a fuel scenario and funding option, deposit the test USDC, and review the payment. You can try a different vendor to see the guard reject it, then approve the original vendor and verify the receipt. The app keeps one demo wallet in memory for the local session.

The synthetic carrier trial lets you inspect 128 funding decisions and download signed receipts for the 50 cases that used the reserve. Funding savings are modeled from the displayed terms. Run the verifier against a receipt you downloaded:

```bash
uv run --locked python -m freightos.verify_receipt path/to/receipt.json
```

## Settlement

The included fork snapshot contains the Kamino vault and lending programs at Solana mainnet slot 471210845. The FreightOS Rust program binds the approved recipient, exact USDC amount, and maximum share burn. The redemption, transfer and settlement record execute in one transaction. The program runs on the local fork. Its source and build script are in [settlement_program](settlement_program/).

The separate `python -m freightos.live --help` command prepares an unsigned mainnet Kamino withdrawal and SPL payment for an eligible wallet. That route uses the existing Kamino and token programs. The FreightOS bill guard and test wallet are part of the local browser flow.

## Verify changes

```bash
uv sync --locked --extra dev
uv run --locked --extra dev pytest -q
```

To rebuild the bundled program, set `SOLANA_PLATFORM_TOOLS` to the extracted Anza v1.58 platform-tools directory and run `bash settlement_program/build.sh`.
