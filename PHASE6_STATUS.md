# Phase 6 implementation status

## Spot/Futures
Implemented foundations:
- market normalization for common spot/perpetual bases
- executable VWAP from both books
- fee/funding/safety adjusted basis edge
- candidate fail-closed evidence gate
- paper position and convergence PnL
- paper exit primitives and replay promotion
- entry/close leg plans
- private balance/margin/borrow gate
- dedicated state machine and incident classification
- asynchronous CCXT-compatible source
- strategy observations and campaign stats

LIVE remains locked. Promotion path:
Discovery -> Paper >= 100 closed -> Replay -> Semi-auto review -> dedicated live acceptance.

## CEX/DEX
Implemented foundations:
- adapter interface
- quote normalization
- route validation
- gas budget
- min-received calculation
- token/network/wallet safety
- paper economics
- dedicated 14-case failure matrix
- execution proof and live lock

No wallet execution adapter is enabled.

## Portfolio / capital
- venue ranking
- capital routing
- total exposure limit
- same-base concentration guard
- manual rebalance recommendation
- per-strategy allocation caps
- withdrawals remain forbidden for automatic trading.

## AUTO
AUTO is not enabled. Eligibility requires:
- >= 50 verified live trades
- profit factor >= 1.3
- drawdown <= 2%
- zero unresolved incidents
- zero UNKNOWN orders
- explicit operator approval
- micro-live limits remain max 1 open trade, <= $5 notional, <= 1x leverage.
Parameter changes require Replay validation and operator approval.
