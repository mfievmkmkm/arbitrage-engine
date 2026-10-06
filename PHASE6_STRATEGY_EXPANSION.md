# Phase 6 roadmap: strategy expansion without weakening Stage 5

Stage 5 Futures/Futures safety invariants remain mandatory and cannot be bypassed by new strategies.

## Spot/Futures
- normalize spot and perpetual symbols
- executable VWAP on both legs
- LONG spot / SHORT future first
- spot-short direction disabled unless borrow availability is explicitly verified
- separate spot balance and futures margin checks
- funding included in holding-period NET
- same persisted intents, private verification, recovery and close acceptance

## Capital routing
- score venues from measured opportunity quality, latency, incidents and realized NET
- fund only a small number of empirically useful venues
- withdrawals remain disabled on trading API keys
- rebalance recommendations are manual initially

## CEX/DEX
- route quote simulation
- gas, swap fee, price impact and slippage are distinct costs
- contract/token validation, tax/mechanics checks, min-received
- chain/network/deposit state before considering executable NET
- separate wallet security boundary
- LIVE remains disabled until dedicated DEX failure matrix passes

## Analytics / AI
- PnL attribution: spread, fees, funding, slippage, other
- venue/pair performance statistics
- AI receives aggregates/anomalies, never authority to place orders
- parameter changes require AI recommendation -> Replay validation -> operator approval
