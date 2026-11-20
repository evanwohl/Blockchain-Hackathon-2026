# FreightOS judge walkthrough

Allow two minutes. Use one narrator and one person at the keyboard. The other two teammates can cover the funding model and settlement questions after the demo.

| Time | Show | Say |
| --- | --- | --- |
| 0:00–0:20 | Overview. Point to the two dates. | “An owner-operator needs $745 for diesel. The main freight invoice pays 23 days later. The driver needs to know what to pay today and how much it costs to bridge that gap.” |
| 0:20–0:50 | Open **See a full bill paid on Solana**. Expand **Compare the fees line by line**. | “Here the customer offers to pay a $180 fuel surcharge early for $5. That supplies $175. FreightOS finds the other $570 in the driver’s Kamino reserve. The driver keeps the full freight invoice. With these terms, estimated fees are $8.49 versus $13.45 if they wait.” |
| 0:50–1:25 | On the same receipt, open **Could an oil contract cover a higher bill?** under the fuel update. Toggle **After I can spend a payout**. | “For the next trip we use recorded Gemini contract prices and quantities. Say oil reaches $93. If diesel is due first, keep $10.51 available. If a winning payout gets there first, the modeled extra cash falls to 24 cents. Same market, one changed date, different advice.” |
| 1:25–1:55 | Return to the receipt at the top. Click **Verify on public Solana**. | “This is the part that actually settled. The customer and carrier co-signed one transaction. The vendor received 1.75 test USDC from the customer through the driver and 5.70 from Kamino. You can verify all 7.45 on public Devnet. Each test cent represents one dollar of the sample bill.” |
| 1:55–2:00 | Leave the verified receipt on screen. | “The driver sees one fuel bill. FreightOS works out which funds arrive in time and pays the approved vendor.” |

### Before presenting

1. Start the packaged app on the presentation laptop. Use the normal local setup in the [README](README.md). Keep the laptop awake and open the Overview, paid receipt at `/public-offer`, and timing switch at `/public-offer#fuel-cover` in separate tabs.
2. Wait for the receipt breakdown and recorded fuel comparison to load. Restore **Before I can spend a payout**. Confirm the projector shows both columns and test the venue network once with **Verify on public Solana**.
3. If verification stalls on stage, use the saved receipt and say “This is the saved finalized transaction.” If the app or projector connection fails, play the captioned MP4 from the laptop. At a 30-second cutoff, go directly to the receipt and explain the two payment sources.

### Questions worth rehearsing

- **Where is the edge?** The engine checks dated cash flows across six Gemini fuel budgets and compares contract terms against spendable reserve proceeds. In the 24-cent plan, 23 cents buys the contract including fees and one cent stays for diesel. If the payout is late at $93 oil, the reserve preview checks the missing $10.50. The payment approval binds the chosen terms and vendor.
- **How real is it?** Both parties used sponsored Devnet funds. The settlement is signed. The $4.96 difference is a modeled fee comparison for this load. The next-trip example prices a hypothetical hedge from recorded offers. No Gemini order was placed.
- **Would borrowing help?** Open `/fuel-credit#reserve-first` only if asked. At a 6.100 SOL pledge limit, the smaller loan fits the recorded 23-day, 40% stress case. The driver uses $19.44 of existing reserves and borrows $170.56. Those two payment legs are separate simulations.

Until judging, prioritize anything that breaks this walkthrough, misstates a number, or leaves the driver unable to identify the next action. Keep new instruments and additional screens for after the presentation.
