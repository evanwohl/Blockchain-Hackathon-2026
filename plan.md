# AutoHedge Logistics: Hackathon Plan and Team Split

Date: November 15, 2026

## 1. Overview

AutoHedge Logistics is an automated hedging system for freight yard operators. An edge device measures yard congestion and truck fuel consumption. A quantitative engine combines these measurements with Gemini prediction-market data and computes a hedge that minimizes tail loss. The primary hedge executes on Solana as on-chain perpetual trades. A secondary, capped leg uses Gemini event contracts. A Solana program enforces risk limits and records each input and decision on-chain.

Hedged exposure: diesel accounts for 25 to 35 percent of fleet operating cost. Demurrage and detention fees range from $150 to more than $400 per container per day.

Cost constraint: the project requires no purchases. All components use team-owned equipment, free software, Solana devnet, and the Gemini sandbox.

## 2. Stack

| Component | Selection | Rationale |
|---|---|---|
| Market data | Gemini Prediction Markets public REST and WebSocket API | Event probabilities for oil-price thresholds, weather, and macroeconomic events. No API key is required for market data. |
| Primary hedge | WTI perpetual on Solana devnet. GMTrade adapter if GMTrade offers a devnet; otherwise `mock_perp`, a program priced from the Pyth WTI feed with the same interface | On-chain execution with no real funds. |
| Secondary hedge | Gemini event contracts in the Gemini sandbox (`api.sandbox.gemini.com`), capped at 20 percent of the hedge budget | Direct payout on discrete events that on-chain replication cannot cover. The sandbox uses no real funds. |
| Oracle | Pyth WTI price feed on Solana | 24/7 price with a confidence interval; free to read. |
| Edge hardware | Team-owned laptop or single-board computer for inference; team-owned smartphone or webcam as camera | No purchases. Inference runs locally; raw video stays on the device. |
| Device signing | Ed25519 key stored in the operating system keychain (or a TPM if the laptop has one) | Solana verifies ed25519 signatures natively. |
| Telematics | Software J1939 simulator on a Linux virtual CAN bus (`vcan` with `python-can`) | Produces real J1939 message formats without CAN hardware. |
| Hedge sizing | CVaR linear program over approximately 10,000 Monte Carlo scenarios (cvxpy with HiGHS) | Minimizes tail loss, including losses from price jumps. |
| LLM | Context input only: regime assessment, log-odds adjustments capped at ±0.5, and a written rationale. Free tier, existing credits, or a locally hosted open-weight model | The LLM does not size trades. Each adjustment is logged and hashed on-chain. |
| Alternative chain | Arc testnet (EVM, USDC as gas): Foundry contracts, `ecrecover` signature checks, mock perpetual priced from Pyth or Chainlink WTI | Applicable if the event offers a Circle or Arc prize. |

Gemini orders execute through the Gemini sandbox API. Order IDs and fills are hashed on-chain by `record_offchain_leg`.

## 3. Scope

Included:

- Edge device on team-owned hardware running YOLO locally and producing a signed congestion score
- Simulated J1939 telematics (idle hours and fuel rate) on a virtual CAN bus
- Live Gemini probabilities for a ladder of oil-price thresholds and one or two freight-related events
- Hedging engine (Section 5) producing `hedge_decision`
- Solana program with attestations, a USDC vault, risk checks, and a pause control, deployed on devnet
- WTI perpetual hedge on devnet, including the convex overlay described in Section 5.4
- Secondary Gemini event-contract leg in the sandbox, within its budget cap
- Dashboard, backtest of four strategies, and a tabletop demonstration yard built from items on hand

Excluded: purchased hardware, mainnet transactions, real funds, multiple facilities, automatic unwind, and a mobile application.

## 4. Architecture

| Stage | Owner | Output |
|---|---|---|
| Edge device | P1 | `congestion_snapshot` with telematics, device-signed |
| Vision models and LLM context | P2 | Model files for P1; LLM adjustments in `macro_snapshot` |
| Market data and hedging engine | P3 | `macro_snapshot`, `hedge_decision` |
| Gemini sandbox orders | P3 | Order IDs and fills |
| Solana program and executor | P4 | Attestation, hedge, rebalance, and off-chain-record transactions; `position_update` |
| Dashboard, backtest, and integration | P5 | Interface, metrics, integrated build |

Services share a SQLite store and a FastAPI layer.

Anchor program `autohedge`:

| Account or instruction | Function |
|---|---|
| `Config` | Stores the agent key, device key, guardian key, per-trade cap, weekly cap, maximum leverage, minimum liquidation buffer, event-contract premium cap, and pause flag. Administered by a Squads multisig on devnet. |
| `post_attestation` | Verifies the device ed25519 signature through the ed25519 instruction sysvar. Stores the snapshot hash, congestion score, Gemini probabilities, model input hash, and CVaR before and after hedging. |
| `execute_hedge` | Requires a current attestation. Checks caps, rolling weekly spend, leverage, and liquidation buffer against the live Pyth WTI price. Transfers devnet USDC from the vault PDA to the perpetual position. |
| `rebalance` | Adjusts the perpetual position within the same limits. Used by the overlay schedule in Section 5.5. |
| `record_offchain_leg` | Stores hashes of Gemini sandbox order IDs and fills. |
| `pause` / `unpause` | Guardian control. Also triggered by a missed device heartbeat. |

Repository:

```
autohedge/
  shared/schemas/  # JSON Schema and pydantic/TypeScript types (P5 maintains)
  firmware/        # P1: capture, local inference runtime, signing, heartbeat
  telematics/      # P1: J1939 simulator and virtual CAN reader
  vision/          # P2: models, tracking, scoring, export
  agent/           # P2: LLM context agent
  macro/           # P3: Gemini data client and sandbox order client, Pyth reader
  hedging/         # P3: exposure, scenarios, fusion, optimizer, overlay schedule
  programs/        # P4: autohedge, mock_perp (Anchor)
  executor/        # P4: TypeScript transaction client
  app/             # P5: Next.js dashboard
  backtest/        # P5: historical replay using hedging/
```

## 5. Hedging engine

### 5.1 Exposure

Loss over horizon H (72 hours, with a 7-day view): `L(H) = Fuel + Demurrage`

- Fuel = idle hours × fuel rate × diesel price + detour miles × gallons per mile × diesel price. Idle hours and fuel rate come from J1939 messages. Fuel cost is linear in diesel price and increases faster than linearly with congestion.
- Demurrage = containers × max(0, delay − free time) × tiered daily rate. Demurrage increases convexly with delay, similar to a call option payoff.
- The linear component is hedged with a base WTI perpetual position. The convex component is hedged with a perpetual overlay that replicates a call spread, and with Gemini event contracts for discrete events (Section 5.4).

### 5.2 Scenario model (approximately 10,000 joint paths)

| Variable | Model | Calibration |
|---|---|---|
| WTI | Geometric Brownian motion with Poisson jumps; jump intensity depends on regime | GARCH(1,1) on Pyth and historical WTI data |
| WTI tail calibration | Jump size and intensity fitted so that simulated P(WTI > K) matches the Gemini threshold ladder | Gemini oil-threshold probabilities |
| Diesel | ΔP_diesel = β × ΔWTI + ε, with ε bootstrapped from history to represent basis risk | Rolling regression of EIA diesel prices on WTI |
| Congestion | Three-state hidden Markov model (normal, backlog, systemic) with a delay distribution per state | Congestion score history (hmmlearn) |
| Freight events | Bernoulli variables with fused probabilities (Section 5.3), correlated with regime and oil jumps through a Gaussian copula | Section 5.3 |

### 5.3 Probability fusion

- Prior: Gemini mid price, adjusted for spread
- Camera evidence: logistic model of P(disruption given congestion features), applied in log-odds
- LLM adjustment: at most ±0.5 log-odds per event, with the reason logged
- Fused probabilities set regime transition rates and event probabilities in the scenario model. They do not trigger trades directly.

### 5.4 Optimizer

Decision variables:

- `q_lin`: base WTI perpetual notional (linear hedge)
- `m`: size of a synthetic WTI call spread with strikes K1 < K2, selected from the Gemini threshold ladder
- `n_k`: quantity of Gemini YES contracts on discrete freight events (secondary leg)

Hedge P&L per scenario = `q_lin × ΔWTI + m × CallSpread(K1, K2) + Σ n_k × (payout_k − ask_k) − replication cost`. Replication cost per unit is precomputed for each scenario by simulating the rebalancing schedule with fees, slippage, and funding.

Objective: minimize CVaR95(L − hedge P&L) + λ × expected cost (fees, slippage, funding, replication error).

Constraints, mirrored in the Solana program:

- Margin within budget; weekly spend within cap
- Leverage within the maximum, including the largest overlay delta
- Liquidation distance of at least 3σ of the horizon WTI move
- Position size within a set fraction of venue depth
- Gemini premium within 20 percent of the hedge budget and within a set fraction of visible order-book depth

Method: Rockafellar–Uryasev linear program solved with cvxpy and HiGHS, under one second for 10,000 scenarios. Strike pairs are chosen by grid search over the ladder, with one linear program per pair.

Baselines: minimum-variance hedge ratio h* = Cov(ΔP_diesel, ΔWTI) / Var(ΔWTI), and a static rule.

### 5.5 Execution and rebalancing

- Target perpetual position = `q_lin + m × Δ_spread(WTI, t, σ)`, where Δ_spread is the call-spread delta computed from GARCH volatility.
- The position is rebalanced when it deviates from target by more than a no-transaction band. The band width follows the Whalley–Wilmott approach and balances tracking error against fees.
- Replication does not protect against discontinuous price gaps. Gap risk is included in the replication error term. The 24/7 Pyth feed and 24/7 perpetual trading reduce gap exposure.
- Expected funding cost is included in the objective. Leverage is set by the liquidation constraint, and the vault adds margin when required.
- Orders are split into TWAP child orders when size is significant relative to depth. Limit bands use the Pyth confidence interval. Stale or wide Pyth prices block execution.
- Gemini event contracts are placed as limit orders, held to resolution, or sold when no longer required and the bid exceeds model value.
- The optimizer runs on each snapshot. Trades occur only when the improvement exceeds transaction cost.
- Exposure is reduced as the horizon shortens or when the regime model reports normal for N consecutive snapshots.

### 5.6 Effectiveness metrics

- Variance reduction, CVaR reduction, hedge cost as a percentage of exposure, and dollar-offset ratio. A dollar-offset ratio of 80 to 125 percent is commonly treated as highly effective in hedge accounting.
- Backtest comparison: no hedge, static rule, minimum-variance, and CVaR optimizer over the same periods and scenarios.
- Each run's input hash, hedge ratio, CVaR before and after, and expected cost are stored in the attestation PDA.

Libraries: numpy, scipy, cvxpy, statsmodels, arch, hmmlearn. Data: EIA diesel prices, WTI history, Pyth, Gemini event prices, simulated J1939 telematics.

## 6. Roles

Each role has one owner. The owner is accountable for the listed outputs and completion criteria. Work outside a role's scope requires agreement with the owning role.

| Role | Title | Owns (directories) | Produces | Consumes |
|---|---|---|---|---|
| P1 | Edge Systems Engineer (hardware) | `firmware/`, `telematics/` | Signed `congestion_snapshot`, telematics fields, heartbeat | Model files from P2 |
| P2 | Computer Vision and LLM Engineer | `vision/`, `agent/` | Model files, congestion scoring logic, disruption likelihood model, LLM adjustments | Frames from P1; Gemini data from P3 |
| P3 | Quantitative Engineer | `macro/`, `hedging/` | `macro_snapshot`, `hedge_decision`, Gemini sandbox orders | Snapshots from P1; LLM adjustments and likelihood model from P2 |
| P4 | Solana Engineer | `programs/`, `executor/` | Deployed devnet programs, transactions, `position_update` | `hedge_decision` from P3; snapshots from P1 |
| P5 | Product and Integration Lead | `app/`, `backtest/`, `shared/schemas/` | Dashboard, backtest results, integrated build, presentation | All outputs |

### P1: Edge Systems Engineer (hardware)

Responsibility: the physical data layer, using only team-owned equipment.

Deliverables:

- Edge device on a team-owned laptop or single-board computer, with a team-owned smartphone (as an IP camera) or webcam
- Capture service that reads frames, runs P2's model locally, and emits snapshots at a configurable interval (15 minutes in operation, about 10 seconds in the demonstration)
- Ed25519 device key in the operating system keychain or TPM; each snapshot and the firmware hash are signed
- J1939 simulator on a Linux virtual CAN bus, emitting PGN 65244 (idle operation), 65257 (fuel consumption), and 65266 (fuel rate); a reader that converts these into the telematics fields
- Heartbeat and timestamp service (NTP); a missed heartbeat triggers `pause`
- Tabletop gate lane built from items on hand (paper cutouts or small vehicles), positioned under the camera
- Production bill-of-materials estimate for one site (estimate only; no purchase)

Completion criteria:

- Signed snapshots arrive at the shared store at the demonstration interval for 30 minutes without interruption
- P4's program verifies the device signature on devnet
- Telematics values change when the simulator scenario changes

Not responsible for: model training, scoring logic, hedging decisions.

### P2: Computer Vision and LLM Engineer

Responsibility: detection, scoring, and the LLM context layer.

Deliverables:

- YOLO fine-tuned on public truck and chassis images and frames captured from the demonstration yard; ByteTrack tracking; regions of interest for the gate queue and yard dwell
- Congestion score: z-score of queue length and dwell time against a rolling time-of-day baseline, mapped to 0 to 1
- Model export for P1's device (ONNX, or TensorRT where available); frame-rate and accuracy benchmarks on that device
- Logistic disruption likelihood model for Section 5.3
- LLM context agent: reads snapshots, Gemini probabilities, and headlines; outputs a regime assessment, log-odds adjustments within ±0.5, and a rationale; temperature 0, strict schema, rule-based fallback

Completion criteria:

- Detection accuracy and frame rate reported on P1's device
- Congestion score separates normal and congested demonstration scenes
- LLM output passes schema validation on every call, and the fallback activates when the LLM is unavailable

Not responsible for: device operation, trade sizing.

### P3: Quantitative Engineer

Responsibility: market data, the hedging engine, and the Gemini sandbox leg.

Deliverables:

- Gemini event selection: a ladder of oil-price thresholds and weather or macroeconomic events (port-strike markets may not be listed)
- Gemini WebSocket client producing spread-adjusted probabilities in `macro_snapshot`
- Section 5 implemented as a pure function, reused by the backtest
- Tail calibration of the WTI model to the Gemini ladder; replication-cost simulator
- Gemini sandbox account, API key, terms accepted through the API, limit orders for event legs, order IDs passed to P4
- Proposed values for all risk limits, delivered to P4

Completion criteria:

- Unit tests confirm every constraint in Section 5.4
- The optimizer achieves lower CVaR than minimum-variance in backtest scenarios
- A `hedge_decision` is produced within 2 seconds of each snapshot

Not responsible for: on-chain enforcement, dashboard.

### P4: Solana Engineer

Responsibility: on-chain programs and transaction execution.

Deliverables:

- `autohedge` as specified in Section 4, deployed on devnet, with tests for every cap, including rejection of an over-cap hedge
- `mock_perp`: long and short positions, Pyth mark price, funding, P&L, liquidation; or a GMTrade adapter if a devnet is available
- Devnet USDC vault PDA, Squads administration, guardian key
- TypeScript executor for `post_attestation`, `execute_hedge` (with TWAP), `rebalance`, and `record_offchain_leg`, emitting `position_update` with Solana Explorer links
- Implementation of P3's risk limit values in `Config`

Completion criteria:

- All instructions pass tests on a local validator and on devnet
- A full cycle (attestation, hedge, rebalance, off-chain record) completes on devnet from a live `hedge_decision`
- Pause stops all trading instructions

Not responsible for: choosing limit values, hedge sizing.

### P5: Product and Integration Lead

Responsibility: schemas, integration, backtest, dashboard, and presentation.

Deliverables:

- Shared schemas in `shared/schemas/`, fixed on day one; mocks for each interface
- docker-compose setup that runs the full system on one laptop
- Backtest over a 2026 oil price shock with a proxy congestion series (Gemini history used only where available; the platform launched in December 2025); four strategies, four metrics
- Dashboard: annotated frame, congestion score, Gemini probabilities, rationale, CVaR before and after, attestation log, positions, overlay delta, remaining cap, and loss compared with hedge P&L
- Demonstration controls: scene selection, clock acceleration, oil price shock, settlement
- Presentation, recorded backup video, and one or two dispatcher interviews with cost figures

Completion criteria:

- The complete system starts with one command
- The backtest table and charts are generated from code
- The demonstration runs within 3 minutes in rehearsal

Not responsible for: component internals owned by P1 to P4.

### Decision ownership

| Decision | Proposes | Approves |
|---|---|---|
| Schema changes after day one | Any role | P5 |
| Risk limit values | P3 | P4 (implementation), team (values) |
| Gemini events used | P3 | Team |
| Model choice and thresholds | P2 | P2 |
| Demonstration script | P5 | Team |

Schedule dependencies: P3 and P4. P5 takes over the Gemini order client if P3 falls behind.

## 7. Interfaces

Schemas are fixed in `shared/schemas/` on day one. Each producing service provides a mock.

`congestion_snapshot` (P1 to P2, P3, P4, P5):

```json
{
  "facility_id": "PORT_HOUSTON_DEPOT_04",
  "device_id": "edge-01",
  "timestamp": 1794760380,
  "gate_queue_trucks": 42,
  "avg_dwell_minutes": 185,
  "dock_utilization": 0.41,
  "congestion_anomaly_score": 0.84,
  "telematics": {"trucks_reporting": 12, "idle_hours_window": 31.5, "fuel_rate_gph_avg": 0.84, "fuel_used_gal_window": 26.4, "source": "j1939_sim"},
  "frame_url": "/frames/1794760380.jpg",
  "firmware_hash": "sha256...",
  "device_pubkey": "base58...",
  "sig": "base58-ed25519..."
}
```

`macro_snapshot` (P3 Gemini data and P2 LLM adjustments, to P3 and P5):

```json
{
  "timestamp": 1794760400,
  "gemini": [
    {"event_ticker": "FROM_EVENTS_API", "instrument_symbol": "FROM_EVENTS_API", "label": "WTI above $K on date", "category": "commodities", "strike": 80.0, "bid": 0.57, "ask": 0.59, "p_mkt": 0.58, "llm_logit_adjustment": 0.2}
  ],
  "wti_pyth": {"price": 74.20, "conf": 0.05, "publish_time": 1794760398}
}
```

`hedge_decision` (P3 to P4 and P5):

```json
{
  "decision_id": "d-0007",
  "timestamp": 1794760410,
  "regime_probs": {"normal": 0.10, "backlog": 0.25, "systemic": 0.65},
  "exposure": {"horizon_h": 72, "expected_loss_usd": 11800, "cvar95_unhedged_usd": 26500},
  "hedge": {"cvar95_hedged_usd": 9900, "expected_cost_usd": 410, "min_var_ratio": 0.82, "variance_reduction": 0.70},
  "base_leg": {"venue": "solana_perp", "asset": "WTI", "side": "long", "notional_usdc": 7000},
  "overlay": {"type": "call_spread", "k1": 78.0, "k2": 86.0, "size_bbl": 900, "current_delta_bbl": 310, "rebalance_band_bbl": 45},
  "target_position_usdc": 9300,
  "leverage": 2,
  "liq_buffer_sigma": 3.4,
  "exec": "twap_4",
  "event_legs": [
    {"venue": "gemini_sandbox", "instrument_symbol": "FROM_EVENTS_API", "outcome": "yes", "qty": 1500, "limit": 0.31, "p_mkt": 0.30, "p_fused": 0.38}
  ],
  "event_premium_share": 0.05,
  "model_inputs_hash": "sha256...",
  "snapshot_hash": "sha256...",
  "rationale": "Systemic regime probability 0.65; dwell time 4x baseline; WTI above $80 probability up 9 points in 24 hours."
}
```

`position_update` (P4 to P5):

```json
{
  "decision_id": "d-0007",
  "status": "executed | rebalanced | rejected_by_policy | failed",
  "solana_signatures": ["5h..."],
  "attestation_pda": "base58...",
  "position": {"venue": "solana_perp", "notional_usdc": 9300, "entry": 74.2, "liq_price": 52.1, "pnl": 0},
  "gemini_order_ids": ["123456"],
  "weekly_cap_remaining_usdc": 14000
}
```

## 8. Milestones (one week)

- Day 1: schemas fixed, mocks, repository; devnet wallets funded from the faucet; Gemini sandbox account created; Gemini events selected; P1 confirms which team-owned devices will be used
- Mid-week checkpoint: complete flow on live data, from edge snapshot and Gemini probabilities through the optimizer to Solana attestation, hedge, and rebalance transactions and a Gemini sandbox order, including on-chain rejection of an over-cap hedge
- End of week: backtest, dashboard, demonstration yard, presentation, backup video, rehearsals
- Remaining time: backtest validation, dispatcher interviews, demonstration refinement

## 9. Demonstration (3 minutes)

| Time | Segment |
|---|---|
| 0:00 | Problem statement and dollar exposure |
| 0:15 | Normal yard conditions: score 0.2, stable probabilities; the agent does not trade |
| 0:35 | Items are moved into the gate lane; score rises to 0.86 and probabilities change; the optimizer displays CVaR before and after hedging and the minimum-variance comparison; attestation and hedge transactions confirm on Solana devnet with Explorer links; the Gemini sandbox order is placed |
| 1:35 | Over-cap request rejected on-chain; guardian pause |
| 2:00 | Accelerated clock: oil price moves, overlay rebalances, event resolves; loss compared with hedge P&L |
| 2:30 | Backtest results (four strategies, four metrics), production hardware estimate, target customers (drayage fleets, third-party logistics providers, port-adjacent warehouses) |

## 10. Evaluation criteria

| Criterion | Supporting evidence |
|---|---|
| Impact | Dispatcher interviews, dollar exposure, percentage of loss offset in backtest |
| Technical depth | Edge inference, device signing, CVaR optimizer with dynamic replication, Anchor program, operating end to end |
| Sponsor technology | Gemini Prediction Markets data and event contracts, Solana, Pyth, Squads |
| Originality | Signed physical data used to drive on-chain hedges |
| Risk controls | On-chain caps, pause control, heartbeat-triggered pause, hashed decision records |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Gemini does not list strike or tariff events | Oil threshold ladder and weather or macroeconomic events; the engine does not depend on specific events |
| Gemini data gaps or latency | Last valid snapshot used within an age limit; stale data reduces hedge size. If the sandbox order client is unavailable, the hedge proceeds with the on-chain leg only |
| No GMTrade devnet | `mock_perp` priced from Pyth WTI with the same interface |
| Replication error during price gaps | Gap risk included in scenarios; 24/7 Pyth feed and perpetual trading; wider liquidation buffer |
| Anchor development delay | Program limited to six instructions; if necessary, caps enforced in the executor with attestations kept on-chain |
| Insufficient inference speed on team-owned hardware | Smaller YOLO variant, lower resolution, or lower frame rate; snapshots require only one frame per interval |
| Devnet faucet limits | Request devnet SOL and USDC early; reuse a single funded vault |
| Basis risk and liquidation | Bootstrapped basis in scenarios; 3σ liquidation constraint; 2x leverage in the demonstration |
| LLM output variation or quota limits | Bounded adjustments, schema validation, rule-based fallback, local model option |
| Network or camera failure at the venue | Cached frames, recorded clips, backup video |

## 12. Extensions

- Hardware secure element and CAN interface for a production device
- Measured boot and remote attestation of edge firmware
- Mainnet deployment and a direct venue adapter with order routing
- Portfolio-level cap across multiple facilities
- Automatic unwind
- Pooled vault for owner-operators
- On-chain parametric event contracts settled by oracle
- Arc deployment
