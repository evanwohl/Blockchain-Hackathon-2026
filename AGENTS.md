# Working on FreightOS

FreightOS helps an owner-operator cover a fuel bill before a customer pays their freight invoice. A driver should be able to answer four questions from the interface. How much is due, when is it due, what will FreightOS send, and what do I still have to pay myself?

## Run a fresh checkout

Use Python 3.11 or later and uv from the repository root.

```bash
uv sync --locked --extra dev
uv run --locked python -m freightos.app
uv run --locked --extra dev pytest -q
```

The server prints its loopback URL, normally `http://127.0.0.1:8783`. Run the tests in a second terminal. The browser uses a single in-memory demo wallet shared by tabs. For automated payment tests, launch a second server with `--port` and a free port so a teammate's open bill keeps its state. Dependencies need installing once. The browser flow then runs against bundled data.

## What the demo is proving

The hackathon track is DeFi & Onchain Applications on Solana, with Gemini prediction-market data. The product edge is the connection between the funding decision and an authorized vendor payment. A mixed-integer optimizer compares dated cash flows and can combine sources or choose cash alone. The onchain program enforces the approved reserve leg. Use the displayed baseline and sample terms when discussing savings.

## Follow one load

- **Overview at `/`.** Compare the three funding situations across the baseline fuel budget and recorded Gemini oil thresholds. Opening a comparison carries the selected plan into the bill. An existing approval or payment record takes precedence. Browsing quotes moves no funds.
- **Fuel bill at `/bill`.** On the default sample, $745 is due on day 5. The $3,000 main invoice pays on day 28. The plan draws $555 from the reserve and gets $190 from taking early pay on a $200 return load. Estimated fees total $13.45 versus $150 to take early pay on the main invoice. The $136.55 difference is modeled fee savings.
- **Settlement.** The demo wallet starts with $600. The bill quotes the smallest whole-cent Kamino deposit that covers the tested budgets for that funding profile. The default setup is $570.01, leaving $29.99 in the wallet. A switch to a profile needing more funds prompts for a top-up. The driver reviews the payee and amount, then approves the reserve transfer. The signed receipt confirms only that transfer. A separate action records the driver's outside payment, with an optional reference. Undoing that entry reopens the outside balance. A bill statement summarizes both sources. A calendar export uses the driver's chosen trip start as day 1.
- **Sample loads at `/trial`.** The bundled trial has 128 synthetic loads. Search by load ID, filter by reserve use, and inspect the plan, alternatives and available signed receipt. Open `/trial#SYN-004` for a specific example.

The Gemini input is a recorded oil-market scenario used to stress the fuel budget. This browser app executes reserve payments on a local Solana fork with test USDC. External invoice and bank payments are modeled or recorded by the driver. Documentation ingestion, broker approval, and live commodity trading belong to the wider product plan. Describe them as roadmap work until they have working entry points.

## How to evaluate the decision engine

- The bill compares four funding menus under identical invoice deadlines. Existing cash and the main invoice are available in each. The engine independently enables reserve funding and outside funding (early pay on the return load and any eligible bank credit). The pairing benefit is the lower fee of the two one-group menus minus the fee when both are available. A cent-level capacity search finds the smallest reserve that preserves that advantage.
- `hedging/invoice_policy.py` fixes invoice elections before fuel cost is known, then reprices funding draws after each modeled fuel outcome. For each election it measures the maximum extra fee relative to choosing with hindsight. It minimizes that maximum over the available elections. The test set includes the original fuel budget and same-expiry upside thresholds from the captured Gemini ladder. Its guarantee covers these inputs and funding terms only. The setup quote simulates a Kamino deposit followed by redemption, including share rounding, to find the smallest whole-cent deposit that covers the required proceeds.
- On Sample loads, the same-reserve comparator tests incremental decision quality. In the bundled synthetic trial it saves $738.82 across 128 cases, with lower fees on 41 and equal fees on 87. The comparator without reserve access measures a different advantage. Keep the baseline visible whenever citing a result.

## Where changes belong

| Concern | Starting point |
| --- | --- |
| Funding constraints and fees | `hedging/quick_pay.py` prices dated cash flows. `funding_plan` in `freightos/app.py` supplies the scenario/profile combinations. |
| Browser and API | `freightos/app.py` serves an explicit asset map and manages settlement actions. `freightos/web/` contains buildless HTML, CSS and ES modules. New browser modules or routes need an asset-map entry. |
| Fork and authorization | `freightos/fork.py` runs LiteSVM, `freightos/reserve.py` constructs reserve instructions, and `freightos/bill.py` binds a decision to the onchain receipt. The Rust program is in `settlement_program/src/lib.rs`. |
| Evidence and receipts | Packaged inputs and snapshots live in `freightos/artifacts/`. Run `uv run --locked python -m freightos.verify_receipt path/to/receipt.json` to replay a signed receipt. The separate `freightos.live` CLI prepares unsigned mainnet transactions. The opt-in public balance check in transfer details reads confirmed mainnet custody data. A public balance is evidence about that account, separate from the demo receipt. |
| Tests and distribution | Start with `tests/test_quick_pay.py` and `tests/test_reserve_settlement.py`. Package-data rules are in `pyproject.toml`. For asset or fixture changes, build a wheel with `uv build --wheel` and exercise the installed app. |

For Rust changes, see the build instructions in `README.md` and `settlement_program/build.sh`. The browser loads the bundled program binary. Rebuild it and replay the settlement tests when changing program behavior. The wider `plan.md` describes product intent. Check the active API, UI and packaged fixtures for what ships today.

## Preserve the payment and design contracts

- Read bill amounts and costs from the selected server plan. Plan fields ending in `_cents` are integer cents. Raw USDC amounts have six decimal places. Source rows must reconcile to both the bill and estimated fees.
- Use the server's current decision, receipt, manual entry and version for financial state. A pending approval binds the recipient, amount and decision. Refresh after a failed response before deciding whether to retry a transfer.
- Keep the driver's decision and next action above the illustration on phones. Follow the existing dark bill surface, paper ledger, engraved slip, condensed amount typography and square controls. Citron marks the fuel reserve. Lavender and hatching mark outside funding. Keep a written label for each.
- Give each action a literal name. A deposit, an approval, a confirmed transfer and a driver-reported payment are distinct events. Keep network jargon and replay details available in disclosures.
- Check cash-only, split funding, partial and fully paid states. Exercise reload, a failed request, keyboard use, and a narrow phone viewport. Keep comments for non-obvious constraints and use readable code for the rest.
