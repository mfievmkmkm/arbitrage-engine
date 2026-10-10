# MASTER REMAINING PLAN

This is the authoritative order for the rest of the build.

## Current checkpoint — 2026-10-10

Historical A–F below is a design checklist, not an assertion that already
integrated Paper/Replay/UI pieces are still missing. Use RELEASE_STATUS_RU.md
for the actual implementation matrix.

Spot/Futures Admission + Session are now registered with explicit spot private
clients and strategy-scoped acceptance. The common monitor owns different
leg symbols/account scopes; scanner entry, dynamic exit, explicit recovery,
funding/result finalization, Telegram control and notifications are wired.
Held spot inventory remains separate from derivative flat proof. Next: account
certification of fee units, IOC/lookup, private history completeness and canary.
Forward Spot/Futures and inventory-backed Spot/Spot now have recorded sequential
IOC/latency/partial-fill stress, bounded recovery, durable evidence and export.

Spot/Spot and Funding live lifecycles are now registered with strategy-scoped
acceptance, shared capacity/private monitor and Telegram. DEX firm/RPC simulation
and native CEX hedge/NET ceiling evidence are integrated. Two-sided CEX/DEX
Paper/Replay, isolated signer/nonce journal, dual-RPC finalized receipts, durable
bridge and explicitly configured automatic entry/paired exit are implemented.
Funding now has recorded IOC stress, per-leg settlement exposure, mature public
rate windows, exact quote books, decision/market time separation and durable
attribution/export. Stress results never credit capital or release LIVE.
Remaining: real account/wallet/venue certification, private funding/fee/fill
completeness, dense Paper/OOS evidence and separately accepted micro-canary.
Futures/Futures now has opt-in IOC -> market wired into production dispatch:
two confirmed zero fills only, full refreshed account/risk/native admission,
durable market VWAP proof, separate expiring venue certification and post-fill
cost checks. A read-only actual derivative costs report reconciles cumulative
intents, native flows, private funding and closed results, with UI/audit export.
Actual cost attribution now spans all five strategies. Cash native flows,
BASE fees, held inventory/allocations and mature funding are rechecked; DEX
receipts, canonical CEX journals, native ETH gas and saved replacement valuation
are reproduced in a read-only snapshot. DEX wallet slippage remains explicitly
unattributed rather than assumed zero. Cash actual post-fill NET breaches use
protective close; the old Spot/Futures minimum-edge clamp is removed.
Resting passive-limit queues are not enabled: aggressive IOC plus separately
certified zero-fill market fallback is the selected small-bankroll entry path.
Queue simulation or future maker execution needs its own evidence and scope.
Public rates use entry-reference model valuation; actual account income, venue
mark-price valuation and limit queues still require their own evidence. No
percentage or file-count substitutes for these checks.

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
