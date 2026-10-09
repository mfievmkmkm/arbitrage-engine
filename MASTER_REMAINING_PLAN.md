# MASTER REMAINING PLAN

This is the authoritative order for the rest of the build.

## Current checkpoint — 2026-10-09

Historical A–F below is a design checklist, not an assertion that already
integrated Paper/Replay/UI pieces are still missing. Use RELEASE_STATUS_RU.md
for the actual implementation matrix.

Next concrete integration: register the tested Spot/Futures Admission + Session
with explicit spot private clients and strategy-scoped acceptance; teach the
common monitor different leg symbols/account scopes and held spot inventory;
wire entry/exit/explicit recovery/control and notifications into main. Do not
reinterpret a cash trade as a same-symbol Futures/Futures RuntimeTrade.

Then complete live Spot/Spot inventory and Funding lifecycles; CEX/DEX remains
quote/simulation/security work, not an assumed executable route. Finish with
per-venue account certification, dense Paper/OOS evidence and separately
accepted canary. No percentage or file-count substitutes for these checks.

## A. Finish integration, not architecture
- wire StrategyRuntime into Telegram and scan loop
- persist Spot/Futures Paper positions and marks
- run Spot/Futures Replay with out-of-sample split
- expose per-strategy statistics
- connect bankroll/funding ledgers to persisted storage
- export Diary/Replay/execution data to CSV and XLSX

## B. Complete strategy 3: Spot CEX <-> Spot CEX
- common symbol universe across venues
- executable two-book VWAP
- pre-positioned inventory requirement
- fees/slippage/transfer opportunity cost
- Paper + Diary + Replay
- no transfer-dependent live trade

## C. Complete strategy 5: Funding arbitrage
- normalized rates and intervals
- settlement calendar
- entry/exit spread risk
- holding-period fees
- Paper/Replay ranking

## D. DEX research -> Paper
- implement real quote-provider adapters
- token/chain metadata
- network/deposit state
- route freshness
- gas and min-received
- simulation/revert handling
- dedicated failure matrix
- no live wallet execution until separate acceptance

## E. Telegram product layer
- Strategies
- Opportunities
- Active trades
- Capital
- Venues with Scan/Paper/Real toggles
- DEX/networks
- Analytics
- AI recommendations
- STOP / Resume / kill controls
- edited live-position message

## F. Operational certification
For every live CEX:
clientOrderId, reduceOnly, private positions, fees, funding, position mode, clock, min notional.
No certification = Scan/Paper only.

## G. Final AUTO
- statistically sufficient Paper/Replay
- out-of-sample validation
- micro-live canary
- verified live sample
- no unresolved UNKNOWN states
- parameter version pinned to release
- explicit operator approval
- AUTO stays within risk limits and cannot self-expand them.
