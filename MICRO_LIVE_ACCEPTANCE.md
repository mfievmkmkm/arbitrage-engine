# Stage 5 micro-live acceptance contract

Micro-live remains disabled by default. Code completion does not authorize real orders.

Required before arming:
- CI, unit and E2E suite green.
- At least 100 paper observations and validated replay gate.
- Private positions trusted on both venues.
- No unresolved/UNKNOWN order intents.
- API keys explicitly verified without withdrawal permission.
- Venue-specific clientOrderId and reduceOnly behavior verified.
- Account fee rates verified; unknown fee/funding fails closed.
- RuntimeStore exactly matches private exposure after restart.
- Entry is not OPEN until private hedge verification succeeds.
- Close is not CLOSED until both private exposures are zero and PnL is accounted.
- Operator STOP and global/venue/pair kill switches functional.
- Initial micro-live: one trade, budget limits from micro_live_budget, no automatic scale-up.

Any UNKNOWN exchange state blocks new entries until reconciliation.
