# AutoHedge Logistics — Hackathon Plan & Team Split

Nov 15, 2026

**Product:** an edge device watches a freight yard and reads truck telematics. A trading engine fuses that with Gemini prediction-market odds and sizes a CVaR-optimal hedge: WTI perps on Solana plus Gemini event contracts. A Solana program enforces risk limits and records every input and decision on-chain.

**Exposure being hedged:** diesel is 25–35% of fleet opex; demurrage and detention run $150–$400+ per container per day.

## 1. Stack decisions

| Component | Choice | Reason |
|---|---|---|
| Prediction markets | Gemini Prediction Markets: public REST + WebSocket; sandbox at `api.sandbox.gemini.com` (separate account) | CFTC-regulated, US-accessible, sandbox orders without real funds |
| Chain | Solana devnet; one tiny mainnet proof trade | WTI perps live on Solana (GMTrade); Pyth 24/7 WTI index; native ed25519 verification; sub-second finality |
| Perp venue | GMTrade WTI perp if devnet/SDK exists, else `mock_perp` priced from Pyth WTI | Real venue where possible, identical interface otherwise |
| Edge hardware | Jetson Orin Nano (or Pi 5 + Hailo), NXP SE050, CAN HAT + J1939 | On-device inference, hardware-signed data, measured fuel burn |
| Hedge sizing | CVaR LP over ~10k Monte Carlo scenarios (cvxpy + HiGHS) | Optimizes tail loss; beats variance-only hedging on jump risk |
| LLM | Context only: regime read, log-odds nudges capped at ±0.5, rationale | Never sizes trades; every nudge logged and hashed on-chain |
| Alternative chain | Arc testnet (EVM, USDC gas): Foundry contracts, `ecrecover`, mock perp on Pyth/Chainlink WTI | Use only if a Circle/Arc prize outweighs Solana's real perp venue |

Gemini is an off-chain exchange: its legs execute via API, and order IDs and fills are hashed on-chain by `record_offchain_leg`.

## 2. Scope

**Must ship**

- Edge box running YOLO on-device → congestion score, signed by the SE050
- J1939 telematics (idle hours, fuel rate) from a truck or a simulator
- Live Gemini odds for an oil-threshold ladder plus 1–2 freight-relevant contracts
- Hedging engine (§4) producing `hedge_decision`
- Solana program: attestations, USDC vault, risk checks, kill switch
- WTI perp leg on Solana; Gemini sandbox orders for the event legs
- Dashboard, 4-strategy backtest, tabletop demo yard

**Cut:** multiple facilities, custom PCB, real funds beyond the proof trade, auto-unwind, mobile app.

## 3. Architecture

| Stage | Owner | Output |
|---|---|---|
| Edge device | P1 (hardware), P2 (models) | `congestion_snapshot` + telematics, SE050-signed |
| Gemini ingest + LLM context | P3, P2 | `macro_snapshot` |
| Hedging engine | P3 | `hedge_decision` |
| Solana program + executor | P4 | attestation tx, hedge tx, `position_update` |
| Gemini order client | P3 | sandbox order IDs |
| Dashboard + backtest | P5 | UI, metrics |

Services share SQLite/Postgres plus a FastAPI layer.

**Anchor program `autohedge`**

- `Config`: agent key, device key, guardian, per-trade cap, weekly cap, max leverage, min liquidation buffer, per-event premium cap, paused flag. Admin = Squads multisig.
- `post_attestation`: verifies the device ed25519 signature via the ed25519 instruction sysvar; stores snapshot hash, score, Gemini odds, model-inputs hash, CVaR before/after.
- `execute_hedge`: requires a fresh attestation; re-checks caps, rolling weekly spend, leverage and liquidation buffer against live Pyth WTI; moves USDC from the vault PDA to the perp leg.
- `record_offchain_leg`: stores the hash of the Gemini order ID and fill.
- `pause` / `unpause`: guardian kill switch; also triggered by a missed device heartbeat.

**Repo**

```
autohedge/
  shared/schemas/  # JSON Schema + pydantic/TS types (all)
  hardware/        # P1: BOM, wiring, enclosure CAD, demo yard
  firmware/        # P1: edge services, SE050 signing, J1939 reader, heartbeat
  vision/          # P2: models, tracking, scoring, TensorRT/Hailo export
  agent/           # P2: LLM context agent
  macro/           # P3: Gemini REST/WS + orders, Pyth reader
  hedging/         # P3: exposure, scenarios, fusion, optimizer, rebalancing
  programs/        # P4: autohedge, mock_perp (Anchor)
  executor/        # P4: decision → Solana txs → position_update (TS)
  app/             # P5: Next.js dashboard
  backtest/        # P5: historical replay via hedging/
```

## 4. Hedging engine

### 4.1 Exposure

`L(H) = Fuel + Demurrage`, H = 72 h (plus 7-day view)

- Fuel = idle hours × fuel rate × P_diesel + detour miles × gal/mile × P_diesel. Idle hours and fuel rate are measured from J1939. Linear in P_diesel, convex in congestion.
- Demurrage = containers × max(0, delay − free time) × tiered daily rate. Convex in delay, call-like.
- Perps hedge the linear diesel component; Gemini binaries supply the convex/jump component.

### 4.2 Scenarios (~10k joint paths)

| Variable | Model | Calibration |
|---|---|---|
| WTI | GBM + Poisson jumps, jump intensity by regime | GARCH(1,1) on Pyth/WTI history |
| Diesel | ΔP_d = β·ΔWTI + ε, ε bootstrapped (basis risk) | Rolling OLS of EIA diesel on WTI |
| Congestion | 3-state HMM: normal / backlog / systemic; delay distribution per state | CV score history (hmmlearn) |
| Events | Bernoulli(p_fused), linked to regime and oil jumps via Gaussian copula | Fusion output (§4.3) |

### 4.3 Probability fusion

- Prior: `p_mkt` = Gemini mid, adjusted for fees and spread
- Evidence: logistic P(disruption | congestion features), applied in log-odds
- LLM: ±0.5 log-odds max per contract, with logged reason
- Edge = p_fused − ask. Buy when edge > costs, or when CVaR reduction justifies the premium within the premium budget.

### 4.4 Optimizer

- Variables: perp notional q; binary quantities nₖ across an oil-strike ladder and other contracts
- Objective: min CVaR₉₅(L − HedgePnL) + λ·E[fees + spread + funding + premium over fair]
- Constraints (mirrored on-chain): margin + premium ≤ budget; weekly spend ≤ cap; leverage ≤ L_max; liquidation distance ≥ 3σ of horizon WTI move; 0 ≤ nₖ ≤ fraction of Gemini depth; per-event premium cap
- Method: Rockafellar–Uryasev LP, cvxpy + HiGHS, < 1 s at 10k scenarios
- Binary ladder (YES on WTI > K₁, K₂, K₃) ≈ stepped call spread: convexity without leverage or funding
- Baselines: minimum-variance ratio h* = Cov(ΔP_d, ΔWTI)/Var(ΔWTI); naive static rule

### 4.5 Execution and rebalancing

- Funding cost from venue history enters the objective; expensive funding shifts weight to binaries
- Leverage set by the liquidation constraint; vault tops up margin
- TWAP child orders when size is material vs depth; limit bands from Pyth confidence; reject stale or wide Pyth prices
- Re-optimize each snapshot; trade only if improvement > costs (no-trade band)
- Rebalance perp when hedge ratio drifts > ±10% from target; scale down as horizon shrinks or HMM reads normal for N snapshots
- Hold binaries to resolution, or sell when not needed and bid > model value

### 4.6 Effectiveness metrics

- Variance reduction %, CVaR reduction %, hedge cost % of exposure, dollar-offset ratio (80–125% = highly effective under common hedge-accounting practice)
- Backtest: no hedge vs naive vs minimum-variance vs CVaR, same periods and scenarios
- Each run's inputs hash, hedge ratio, CVaR before/after and expected cost stored in the attestation PDA

**Libraries:** numpy, scipy, cvxpy, statsmodels, arch, hmmlearn. **Data:** EIA diesel, WTI history, Pyth, Gemini events/prices, J1939.

## 5. Team

| | Role | Owns | Stack |
|---|---|---|---|
| **P1** | Hardware & edge | Edge box, camera rig, SE050 signing, J1939 telematics, heartbeat/tamper, demo yard | Jetson Orin Nano or Pi 5 + Hailo, SE050, CAN HAT, 3D-printed enclosure |
| **P2** | Vision & LLM | YOLO fine-tune, tracking, scoring, edge export, disruption likelihood model, LLM context agent | Ultralytics, ByteTrack/`supervision`, TensorRT/Hailo, scikit-learn, LLM API |
| **P3** | Quant & Gemini | §4 end to end, Gemini feeds and sandbox orders | numpy, cvxpy, statsmodels, arch, hmmlearn, Gemini REST/WS |
| **P4** | Solana | `autohedge`, `mock_perp`, Pyth, executor, Squads | Rust/Anchor, TypeScript, `@solana/web3.js` |
| **P5** | Backtest & product | Backtest + metrics, dashboard, demo controller, deck, video, integration | pandas, Next.js, Recharts, wallet adapter, docker-compose |

Critical paths: P3, P4, and hardware lead time (order parts day one). P5 takes the Gemini order client if P3 slips.

**P1 deliverables**

- Laptop + webcam + software key stand-in on the final schema until parts arrive
- Edge box running P2's model on-device; raw video never leaves the site
- SE050 non-exportable Ed25519 key signs snapshot + firmware hash
- J1939 via CAN: PGN 65244 (idle operation), 65257 (fuel consumption), 65266 (fuel rate)
- Heartbeat, enclosure tamper switch, GPS/NTP time; missed heartbeat pauses hedging
- Tabletop gate lane with toy trucks under the camera; BOM and per-site cost

**P2 deliverables**

- YOLO fine-tuned on truck/chassis images plus demo-yard frames; ByteTrack; ROI polygons for gate queue and yard dwell
- Score = z-score of queue and dwell vs rolling time-of-day baseline, mapped to 0–1
- TensorRT or Hailo export; on-device FPS and accuracy benchmarks
- Logistic disruption-likelihood model for §4.3
- LLM context agent: temperature 0, strict schema, rules fallback

**P3 deliverables**

- Gemini contract selection: oil-threshold ladder + weather/macro contracts (port-strike markets may not exist)
- WebSocket prices → fee/spread-adjusted probabilities + depth
- §4 engine as a pure function (reused by backtest); tests for every constraint and CVaR vs minimum-variance
- Gemini sandbox: account, API key, terms accepted via API, limit orders, order IDs to P4

**P4 deliverables**

- `autohedge` per §3 with tests for every cap, including on-chain rejection of an over-cap hedge
- `mock_perp`: long/short, Pyth mark, funding, PnL, liquidation
- GMTrade devnet/SDK check → real adapter, or mainnet proof trade + mock for demo
- Devnet USDC vault PDA, Squads admin, guardian key
- TS executor: post_attestation → execute_hedge (TWAP) → record_offchain_leg → position_update with Explorer links

**P5 deliverables**

- Backtest over a 2026 oil shock with proxy congestion; Gemini history only where it exists (launched Dec 2025); 4 strategies × 4 metrics
- Dashboard: annotated frame, score gauge, Gemini odds, rationale, CVaR before/after, attestation log, positions, remaining cap, loss vs hedge P&L
- Demo controller: feed switch, clock fast-forward, oil price shock, settlement
- Deck, backup video, 1–2 dispatcher interviews with real cost figures

## 6. Interfaces

Frozen in `shared/schemas/` on day one; each producer ships a mock.

**`congestion_snapshot`** — P1 → P2, P3, P4, P5

```json
{
  "facility_id": "PORT_HOUSTON_DEPOT_04",
  "device_id": "edge-01",
  "timestamp": 1794760380,
  "gate_queue_trucks": 42,
  "avg_dwell_minutes": 185,
  "dock_utilization": 0.41,
  "congestion_anomaly_score": 0.84,
  "telematics": {"trucks_reporting": 12, "idle_hours_window": 31.5, "fuel_rate_gph_avg": 0.84, "fuel_used_gal_window": 26.4},
  "frame_url": "/frames/1794760380.jpg",
  "firmware_hash": "sha256...",
  "device_pubkey": "base58...",
  "sig": "base58-ed25519..."
}
```

**`macro_snapshot`** — P3 (Gemini) + P2 (LLM nudges) → P3, P5

```json
{
  "timestamp": 1794760400,
  "gemini": [
    {"event_ticker": "FROM_EVENTS_API", "instrument_symbol": "FROM_EVENTS_API", "label": "WTI above $K on date", "category": "commodities", "bid": 0.57, "ask": 0.59, "depth_usd": 12000, "p_mkt": 0.58, "llm_logit_nudge": 0.2}
  ],
  "wti_pyth": {"price": 74.20, "conf": 0.05, "publish_time": 1794760398}
}
```

**`hedge_decision`** — P3 → P4, P5

```json
{
  "decision_id": "d-0007",
  "timestamp": 1794760410,
  "regime_probs": {"normal": 0.10, "backlog": 0.25, "systemic": 0.65},
  "exposure": {"horizon_h": 72, "expected_loss_usd": 11800, "cvar95_unhedged_usd": 26500},
  "hedge": {"cvar95_hedged_usd": 9800, "expected_cost_usd": 420, "min_var_ratio": 0.82, "variance_reduction": 0.71},
  "legs": [
    {"venue": "solana_perp", "asset": "WTI", "side": "long", "notional_usdc": 9000, "leverage": 2, "liq_buffer_sigma": 3.4, "exec": "twap_4"},
    {"venue": "gemini_pm", "instrument_symbol": "K1", "outcome": "yes", "qty": 1800, "limit": 0.41, "p_mkt": 0.40, "p_fused": 0.47},
    {"venue": "gemini_pm", "instrument_symbol": "K2", "outcome": "yes", "qty": 2600, "limit": 0.19, "p_mkt": 0.18, "p_fused": 0.24}
  ],
  "model_inputs_hash": "sha256...",
  "snapshot_hash": "sha256...",
  "rationale": "Systemic regime 65%; dwell 4x baseline; oil-threshold odds +9 pts/24h"
}
```

**`position_update`** — P4 → P5

```json
{
  "decision_id": "d-0007",
  "status": "executed | rejected_by_policy | failed",
  "solana_signatures": ["5h..."],
  "attestation_pda": "base58...",
  "gemini_order_ids": ["123456", "123457"],
  "positions": [{"venue": "solana_perp", "notional": 9000, "entry": 74.2, "liq_price": 52.1, "pnl": 0}],
  "weekly_cap_remaining_usdc": 14000
}
```

## 7. Milestones (1 week)

- **Day 1:** schemas frozen, mocks, repo; hardware ordered; Gemini sandbox account; devnet wallets funded; Gemini contracts chosen
- **Mid-week (gate):** full flow on real data — edge snapshot → Gemini odds → optimizer → Solana attestation + hedge + Gemini sandbox order; over-cap hedge rejected on-chain
- **End of week:** backtest, dashboard, demo yard, deck, backup video, rehearsals
- Spare time goes to: backtest rigor, dispatcher interviews, mainnet proof trade, demo polish

## 8. Demo (3 min)

1. **0:00** Problem and dollar exposure
2. **0:15** Normal yard: score 0.2, odds flat → agent holds (noise filtering)
3. **0:35** Judge pushes toy trucks into the gate lane → score 0.86, odds move → optimizer shows CVaR before/after vs minimum-variance → attestation + hedge confirm on Solana (Explorer link) + Gemini sandbox order
4. **1:35** Over-cap request rejected on-chain; guardian pause
5. **2:00** Fast-forward: oil moves, event resolves → loss vs hedge P&L
6. **2:30** Backtest table (4 strategies × 4 metrics), hardware BOM/cost, target customers (drayage fleets, 3PLs, port-adjacent warehouses)

## 9. Judging map

| Criterion | Evidence |
|---|---|
| Impact | Dispatcher quotes, $ exposure, % loss offset in backtest |
| Technical depth | Edge AI + secure element + CVaR optimizer + Anchor program, live end to end |
| Sponsor tech | Gemini PM API + sandbox, Solana, Pyth, Squads |
| Novelty | Hardware-signed physical data driving on-chain hedges |
| Trust | On-chain caps, kill switch, heartbeat pause, hashed AI decisions |

## 10. Risks

| Risk | Mitigation |
|---|---|
| No Gemini strike/tariff markets | Oil ladder + weather/macro contracts; engine is market-agnostic |
| Gemini sandbox setup delay | Public data needs no key; set up account day 1 |
| No devnet/SDK for Solana perp venue | `mock_perp` on Pyth WTI + mainnet proof trade |
| Anchor slips | Keep 5 instructions; worst case caps in executor, attestations on-chain |
| Hardware lead time / bring-up | Laptop + webcam + software key + J1939 simulator on the same schema |
| Basis and liquidation | Bootstrapped basis in scenarios; 3σ liquidation constraint; 2x leverage in demo |
| LLM drift | Bounded nudges, schema validation, rules fallback |
| Thin Gemini books | Depth constraint, limit orders, max price per leg |
| Venue Wi-Fi / camera | Cached frames, recorded clips, backup video |

## 11. Stretch

Multi-facility portfolio cap · measured boot / remote attestation · custom PCB · real perp adapter with routing · auto-unwind · pooled vault for owner-operators · Arc deployment
