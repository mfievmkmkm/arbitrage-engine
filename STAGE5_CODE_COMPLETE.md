# Stage 5 code-complete checklist

Implemented:
- persisted order intents and deterministic client IDs
- UNKNOWN order reconciliation path
- private position verification on entry and close
- protective reduce-only flatten
- actual fill/VWAP/fee accounting
- RuntimeStore crash recovery
- exact runtime/private exposure reconciliation
- global, venue and pair kill switches
- operator STOP and evidence-gated RESUME
- daily loss and equity drawdown stops
- one-trade micro-live capacity
- $5 / 10% micro-live notional budget
- 1x initial leverage gate
- verified fee/funding requirements
- funding timing guard
- stale book/private snapshot guards
- liquidity and margin reserves
- minimum order and symbol compatibility gates
- price/spread shock guards
- dynamic NET trailing/time/opportunity exit inputs
- close recovery and private-flat acceptance
- replay promotion gate
- venue capability/approval separation
- failure matrix and acceptance runner
- atomic live metrics
- safe shutdown/crash phase policies

Still operationally required before real orders:
1. CI for the final HEAD must be green.
2. Venue-specific dry-run evidence for clientOrderId/reduceOnly/position mode.
3. Real account fee/funding metadata verification.
4. Paper/Replay statistical promotion criteria must pass.
5. Operator explicitly enables LIVE; defaults remain disabled.
