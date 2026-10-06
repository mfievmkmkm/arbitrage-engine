# ORIGINAL DESIGN AUDIT

This document maps the implementation to the design agreed before coding.

## Core strategy order
1. CEX Futures <-> CEX Futures — implemented through Discovery, Paper, Replay and fail-closed micro-live foundations.
2. Spot <-> Futures — Discovery/Paper/Replay pipeline under integration; LIVE locked.
3. Spot CEX <-> Spot CEX — planned, not implemented yet.
4. CEX <-> DEX — safety/quote/paper foundations; no wallet execution.
5. Funding arbitrage — funding is modeled in existing strategies; standalone funding strategy not implemented yet.

## Required economics
- RAW spread — implemented.
- Executable spread / order-book VWAP — implemented.
- NET after fees/funding/slippage/safety — implemented in core; strategy-specific models exist.
- Actual fills define real entry — implemented in live lifecycle.
- Funding interval/timing — implemented fail-closed when unknown.
- DEX gas / fee / impact / slippage separated — foundations implemented.

## Execution invariants
- no blind dual MARKET assumption
- persisted order intents
- deterministic client IDs
- partial-fill handling
- one-leg recovery
- UNKNOWN submit reconciliation
- private position verification
- close only after private exposure=0
- reduce-only futures closes
- crash/restart policy
- global/venue/pair kill switches
- operator STOP / evidence-gated RESUME
Implemented in Stage 5 foundations.

## Exit engine
Implemented foundations:
- convergence
- NET trailing
- time stop
- adverse conditions
- opportunity cost
Still to complete: unified production policy using replay-selected parameters and optional partial exits only if Replay proves value.

## Discovery / Diary / Replay
Implemented:
- opportunity collection
- Paper
- marks
- Replay
- execution events
- order intents
- strategy observations
Still to complete:
- richer skipped-signal replay
- full CSV/XLSX export
- parameter grid/out-of-sample automation
- long-run venue-pair reports.

## Capital / risk
Implemented:
- small-bank micro-live budget
- 1x initial leverage
- daily stop
- drawdown stop
- max open trades
- portfolio exposure
- venue ranking
- manual rebalance recommendation
Still to complete:
- persistent bankroll ledger
- realized funding settlement ledger
- venue allocation UI.

## Telegram
Implemented: Discovery/Paper/Diary/Replay/Risk/Startup/LIVE controls.
Still to complete:
- unified multi-strategy dashboard
- active live position edited message
- Capital / DEX / Analytics / AI pages
- venue Scan/Paper/Real toggles
- dedicated STOP confirmation UX.

## DEX
Implemented safety architecture only.
Still required:
- real aggregator adapters
- chain/token metadata providers
- deposit/network mapping
- route freshness
- wallet transaction simulation
- approvals/nonces/reorg handling
- dedicated DEX Replay/failure acceptance
- isolated wallet execution only after acceptance.

## AI
Implemented governance foundations:
AI -> recommendation -> Replay validation -> operator approval.
Still required:
- report generation from aggregates/anomalies
- recommendation history
- no direct execution authority (must remain invariant).

## AUTO
Eligibility foundations exist but AUTO remains locked.
Required before final AUTO:
- operational venue evidence
- statistically sufficient Paper/Replay
- verified micro-live history
- canary pass
- zero unresolved incidents/orders
- explicit operator approval.

## Important omissions caught by this audit
These are NOT forgotten and must be built before final completion:
1. Spot CEX <-> Spot CEX strategy.
2. Standalone funding-arbitrage scanner.
3. Full export/reporting.
4. Skipped-signal replay.
5. Persistent bankroll/funding ledger.
6. Telegram multi-strategy + capital/DEX/AI UI.
7. Real DEX provider adapters and chain execution safety.
8. Production unified Exit policy and Replay parameter promotion.
9. Venue-specific operational certification.
10. Rebalance analytics/history.

The implementation must continue in the original strategy order and may not enable a later strategy's LIVE mode before its own acceptance gate.
