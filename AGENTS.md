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

- **Overview at `/`.** The first screen opens on a priced load. Walk through this bill opens that plan. Change the funding or fuel estimate expands optional inputs. Follow the money explains which part comes from Gemini, how FreightOS chooses funds, and which payment runs on Solana. Verify sample payment replays the bundled SYN-004 receipt in a separate fork without touching the current bill. With bank credit, choose lowest fees or keep cash for the road. Both choices show the estimated fees and what stays in the demo wallet after setup. Opening a comparison carries the selected plan into the bill. An existing approval or payment record takes precedence. Browsing quotes moves no funds.
- **Fuel bill at `/bill`.** On the default sample, $745 is due on day 5. The $3,000 main invoice pays on day 28. The plan draws $555 from the reserve and gets $190 from taking early pay on a $200 return load. Estimated fees total $13.45 versus $150 to take early pay on the main invoice. Review reserve setup opens the amount breakdown before any deposit. On phones, Plan & fees and Dates & backup give direct access to each view. The schedule groups transactions by day. Compare fuel estimates lets the driver select the original bill or a recorded Gemini oil level and immediately see the reserve payment and fees. Browsing estimates moves no funds. The estimate locks during approval and after payment. The $136.55 difference is modeled fee savings.
- **Settlement.** The demo wallet starts with $600. The bill quotes the smallest whole-cent Kamino deposit that covers the tested budgets for that funding profile. The default setup is $570.01, leaving $29.99 in the wallet. A switch to a profile needing more funds prompts for a top-up. The driver reviews the payee and amount, then approves the reserve transfer. The signed receipt confirms only that transfer. A separate action records the driver's outside payment, with an optional reference. Undoing that entry reopens the outside balance. A bill statement summarizes both sources. The payment schedule and calendar export include bank repayment with fees and planned reserve replenishment. The trip start is day 1.
- **Sample loads at `/trial`.** The bundled trial has 128 synthetic loads. The first comparison gives both strategies access to the same reserve. Search by load ID, filter by reserve use, and verify a signed payment or download its receipt. Open `/trial#SYN-004` for a specific example.

- **Return unused fuel money.** After a deposit, the bill offers Check unused money when the vendor payment has no pending approval. Before payment the quote preserves enough redeemable proceeds for every tested budget in the selected plan. After vendor settlement it can return all remaining shares to the driver's wallet. The driver explicitly confirms the return. The vendor receipt and the outstanding outside payment keep their original amounts. On a fresh original-estimate quick-pay bill, a $570.01 deposit and $510 vendor payment leave 60.007991 USDC to return, taking the wallet from $29.99 to 89.997991 USDC. A return is the driver's own capital. Keep it separate from fee savings.

The Gemini input is a recorded oil-market scenario used to stress the fuel budget. This browser app executes reserve payments on a local Solana fork with test USDC. External invoice and bank payments are modeled or recorded by the driver. Documentation ingestion, broker approval, and live commodity trading belong to the wider product plan. Describe them as roadmap work until they have working entry points.

- **Deadline and backup.** On the default bill, reserve money is expected on day 4 and can arrive through day 5. A later arrival leaves a $555 shortfall in the original plan. Under Dates & backup, compare and select an alternative before arranging invoice terms. Without the reserve, early pay on the main invoice releases $2,850 for a $150 fee. With an eligible bank line, funding the $745 bill costs $7.45. Payment options also has a Plan without the fuel reserve checkbox. These plans use outside funding, can be recorded with a reference, and produce a bill statement. A pending approval or recorded payment locks the selection.

## Public Devnet demo

On Overview, **See the paid example** opens `/public-payment`. It shows the completed public version of FOS-2048. One test cent represents each modeled dollar. The operator acquired Circle Devnet USDC through Orca, deposited 5.7001 test USDC into an existing Kamino vault, approved the demo fuel company, then redeemed shares and paid it 5.55 test USDC. The $190 outside portion and fee comparison are modeled. The three transaction links and saved receipt work without a connected wallet. Verify on public Solana checks finalized signatures, instructions, token deltas and the settled FreightOS receipt via RPC. The practice bill at `/bill` continues to execute locally.

Run `uv run --locked python -m freightos.devnet verify` for the same public check. `freightos/artifacts/devnet/sample.json` contains the signed records and approved decision. The Devnet feature in the Rust guard pins the test mint, vault and vault program. Its public address is `6UQBiPuy7YZcFBtGZFHTQ39CyViKnA6FYMjEWgYQtrMq`. The default Rust build uses the original fork addresses. The stripped Devnet binary built with Anza v1.58 and Cargo feature `devnet` matched the downloaded deployment byte for byte, SHA-256 `d7f8927c038d66359f14fb952c6ab68a4bd5440b057fa907784c00b0e317a45d`.

For a fresh public run use a dedicated Devnet payer holding at least 5.7001 Circle test USDC and test SOL for account rent and fees. The CLI reads the keypair only in the operator’s process. Run `python -m freightos.devnet approve --payer-file PATH` to deposit and bind the payment in one transaction. Wait for the Explorer confirmation, then run `python -m freightos.devnet pay --payer-file PATH`. Each printed signature opens on Devnet. Reusing the same payer and load cannot pay twice. The published judge page stays bound to the original receipt.

## Presenting to a first-time judge

Open Overview on a fresh demo. Point out the $745 fuel bill, the 23-day gap and the two fee amounts on the slip. Early pay on the smaller $200 return-trip invoice yields $190. That lets the driver cover the rest from the reserve while collecting the full $3,000 main invoice later. Walk through this bill opens the actual approval flow. For a quick proof without changing the bill, Verify sample payment replays a **different** completed load, SYN-004, and checks that its vendor received $541.68 in test dollars. The 128-load comparison measures modeled fees under synthetic terms. The signed receipts establish what the reserve actually transferred on the fork.

## How to evaluate the decision engine

- The bill compares four funding menus under identical invoice deadlines. Existing cash and the main invoice are available in each. The engine independently enables reserve funding and outside funding (early pay on the return load and any eligible bank credit). The pairing benefit is the lower fee of the two one-group menus minus the fee when both are available. A cent-level capacity search finds the smallest reserve that preserves that advantage.
- `hedging/invoice_policy.py` fixes invoice elections before fuel cost is known, then reprices funding draws after each modeled fuel outcome. For each election it measures the maximum extra fee relative to choosing with hindsight. It minimizes that maximum over the available elections. The test set includes the original fuel budget and same-expiry upside thresholds from the captured Gemini ladder. Its guarantee covers these inputs and funding terms only. The setup quote simulates a Kamino deposit followed by redemption, including share rounding, to find the smallest whole-cent deposit that covers the required proceeds.
- For the two bank-credit choices, the optimizer also finds the least reserve capacity that covers every tested bill within a fee allowance. It evaluates each shared invoice election, minimizes each scenario’s reserve draw subject to its fee cap, then takes the largest required draw. At that common capacity it reprices the draws. The benchmark is each scenario’s minimum fee with the full funding menu and hindsight. On a fresh $600 demo wallet and $745 bill, lowest fees sets aside $599.01 and costs $5.02. Keeping road cash sets aside $464.01 and costs $6.02, leaving $135.99 available. The $135.00 difference is liquidity, and the extra fee is $1.00. The bank repayment is $283.81 on day 28 in the road-cash plan. Setup quotes reflect the current wallet, including earlier deposits. Credit receipts carry the cash policy version and older unversioned decisions still replay.
- `hedging/payment_timing.py` holds the original invoice elections and draws fixed, withholds the selected reserve proceeds, and sums dated receipts, fees, bills and repayments against the cash floor. The first deficit identifies when the reserve is needed. The deadline also respects the reserve maturity. Receipts scheduled on the same day arrive before bills under this model. The sample-load stress test prices a separate one-day delay with original fees. Across 128 synthetic loads, 11 original plans miss a deadline, short $3,738.05 across their first missed dates. Replanning for that delay upfront costs an extra $172.03 across all loads.
- On Sample loads, the same-reserve comparator tests incremental decision quality. In the bundled synthetic trial it saves $738.82 across 128 cases, with lower fees on 41 and equal fees on 87. The comparator without reserve access measures a different advantage. Keep the baseline visible whenever citing a result.

## Where changes belong

| Concern | Starting point |
| --- | --- |
| Funding constraints and fees | `hedging/quick_pay.py` prices dated cash flows. `funding_plan` in `freightos/funding.py` supplies the scenario/profile combinations. |
| Browser and API | `freightos/app.py` serves an explicit asset map and manages settlement actions. `freightos/web/` contains buildless HTML, CSS and ES modules. New browser modules or routes need an asset-map entry. |
| Fork and authorization | `freightos/fork.py` runs LiteSVM, `freightos/reserve.py` constructs reserve instructions, and `freightos/bill.py` binds a decision to the onchain receipt. The Rust program is in `settlement_program/src/lib.rs`. |
| Reserve returns | `freightos/reserve_return.py` simulates two successive Kamino redemptions when a bill needs a protected balance. It searches in integer vault shares for the largest supported return, checks the signed transfer against the quote, and binds it to the driver’s wallet. The return has a separate downloadable receipt. The receipt CLI replays both formats. |
| Evidence and receipts | Packaged inputs and snapshots live in `freightos/artifacts/`. Run `uv run --locked python -m freightos.verify_receipt path/to/receipt.json` to replay a signed receipt. The separate `freightos.live` CLI prepares unsigned mainnet transactions. The opt-in public balance check in transfer details reads confirmed mainnet custody data. A public balance is evidence about that account, separate from the demo receipt. |
| Tests and distribution | Start with `tests/test_quick_pay.py` and `tests/test_reserve_settlement.py`. Package-data rules are in `pyproject.toml`. For asset or fixture changes, build a wheel with `uv build --wheel` and exercise the installed app. |

For Rust changes, see the build instructions in `README.md` and `settlement_program/build.sh`. The browser loads the bundled program binary. Rebuild it and replay the settlement tests when changing program behavior. The wider `plan.md` describes product intent. Check the active API, UI and packaged fixtures for what ships today.

## Preserve the payment and design contracts

- Read bill amounts and costs from the selected server plan. Plan fields ending in `_cents` are integer cents. Raw USDC amounts have six decimal places. Source rows must reconcile to both the bill and estimated fees.
- Use the server's current decision, receipt, manual entry and version for financial state. A pending approval binds the recipient, amount and decision. Refresh after a failed response before deciding whether to retry a transfer.
- Keep the driver's decision and next action above the illustration on phones. Follow the existing dark bill surface, paper ledger, engraved slip, condensed amount typography and square controls. Citron marks the fuel reserve. Lavender and hatching mark outside funding. Keep a written label for each.
- Give each action a literal name. A deposit, an approval, a confirmed transfer and a driver-reported payment are distinct events. Keep network jargon and replay details available in disclosures.
- Check cash-only, split funding, partial and fully paid states. Exercise reload, a failed request, keyboard use, and a narrow phone viewport. Keep comments for non-obvious constraints and use readable code for the rest.

## Shipping changes

Keep this guide current when a change affects the demo, numbers, integrations, or teammate tasks. Ship verified product changes on a scoped feature branch, merge into main and verify the remote main commit. In a mixed research worktree, build and test the tracked release in isolation and leave unrelated work in place. Exercise the installed wheel from outside the checkout so packaged data and browser assets resolve as they will for teammates.
