# FreightOS

Fuel is due. Freight pays later. FreightOS compares ways to bridge that gap and binds the selected reserve payment to the approved fuel vendor on Solana.

## Start here for judges

Run the app below, then open [the three-minute demo](http://127.0.0.1:8783/judge). It connects the funding decision, a completed co-signed public payment and the same-reserve benchmark. No wallet connection is needed. The saved evidence works offline after installation; public verification is optional.

The core result is a payment, not just a quote: a customer contribution and a Kamino vault withdrawal reached the fuel vendor in one co-signed transaction. The FreightOS program enforces the approved recipient, reserve amount and maximum share burn. The verifier independently reconciles signatures, instructions, token deltas and the settled program record.

## Three-minute presentation

1. **The driver’s problem (30 seconds).** Open Overview. The sample driver owes $745 for fuel on day 5 but receives the $3,000 freight invoice on day 28. FreightOS compares dated receipts and obligations before choosing which invoice, reserve or credit source to use. The default practice plan estimates $13.45 in fees versus $150 to accelerate the main invoice. That $136.55 difference is modeled under the displayed terms.
2. **The public proof (75 seconds).** Open Judge demo, then the full paid receipt. This separate customer-offer example accelerates a $180 surcharge for a $5 fee. The customer supplies $175 and Kamino supplies $570 at bill scale. On public Devnet, both parties signed the same transaction and the vendor received 7.45 test USDC: 1.75 from the customer and 5.70 from Kamino. One test cent represents one modeled dollar. Click **Verify on public Solana**, or download the signed evidence. The offer costs $8.49 versus $13.45 for waiting, a modeled $4.96 fee reduction. These are different terms from the default practice bill.
3. **The fair comparison (45 seconds).** Open Sample loads with the same reserve available to both strategies. FreightOS reduces modeled fees by $738.82 across 128 synthetic loads: 41 improve and 87 tie. The baseline chooses the cheaper of conventional funding and reserve-plus-invoices funding. This measures the benefit of combining funding sources, rather than simply giving one strategy more capital. An identical manual optimization ties FreightOS.
4. **The close (30 seconds).** “FreightOS turns a fuel-funding decision into a vendor payment the driver can authorize and a judge can verify.” Show the practice bill for interaction. Keep the signed public receipt available as the proof.

For a demo without internet access, use the verified saved receipt, the local practice bill and the bundled trial. A failed public refresh preserves the last checked result and labels its source. The browser shares one in-memory practice wallet between tabs; use a separate server port for a fresh presentation.

## Integration and scope

| Component | Working role | Evidence |
| --- | --- | --- |
| Gemini | Recorded oil thresholds and settlements stress the modeled fuel bill across six budgets. | Packaged market responses and evidence hashes; no commodity order is placed. |
| Funding optimizer | Compares dated cash flows with one invoice election across fuel outcomes and scenario-specific draws. | Reproducible decisions, fee breakdowns and 128 synthetic loads. |
| Kamino | Redeems the driver’s USDC vault shares for the authorized payment. | Signed public Devnet withdrawal and local-fork execution. |
| Solana / FreightOS guard | Enforces the reserve payment’s recipient, exact amount and share limit, with atomic settlement. | Rust source, finalized public transactions and replayable receipts. |

The driver supplies reserve capital. This is a working test-fund prototype, with modeled fees and freight terms. It does not establish customer traction, eliminate invoice default or vault risk, or execute live invoice financing. Invoice ingestion, broker approval and live commodity trading remain roadmap work. The separate `/trip-cover` and `/forward-fill` screens expose additional planning and paper research; they are optional during the core presentation.

## Public payment demo

Open **See a full bill paid on Solana** on Overview. For a $745 sample fuel bill the demo customer pays 1.75 test USDC through the driver, and Kamino supplies 5.70 test USDC. The customer and driver signed the same transaction. The vendor received all 7.45 test USDC on public devnet. Each test cent represents one modeled dollar. [View the finalized settlement on Solana Explorer](https://explorer.solana.com/tx/2MAefNg3M2xFj27U82XEg2bwBQDhnmSUD24Ta4s7o47nN4hu5T1dMTKwyhAtHRZR9TQpFa2rAVa1yrNPx1QTGzvc?cluster=devnet)

Run `uv run --locked python -m freightos.public_offer --live` to verify the offer terms, both signatures, vault withdrawal and vendor balance. The original reserve-only example is available at `/public-payment` and through `python -m freightos.devnet verify`. See [the project guide](AGENTS.md) for the demo flow and execution details.

FreightOS compares ways to fund a load’s fuel bill, shows how a recorded Gemini oil scenario changes the cash needed, and pays the approved vendor from a Kamino USDC reserve. The browser app uses test funds on a local Solana fork. It runs offline after dependency installation.

## Open the demo on a phone

[Create a private demo on GitHub Codespaces](https://codespaces.new/evanwohl/Blockchain-Hackathon-2026). Sign in and tap **Create codespace**. When setup finishes, open **Ports → FreightOS demo (8783) → Open in Browser** if the app has not opened automatically. This runs the full app with a fresh practice wallet. Stop the codespace when finished to end compute usage.

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
