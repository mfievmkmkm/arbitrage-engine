# TELEGRAM CONSOLE V2

The bot is now structured as a serious operator console.

## Visual hierarchy
1. Critical incident banner
2. System / risk / scanner / LIVE state
3. Strategy flow counts
4. Positions and capital
5. Research / analytics
6. Configuration and exports

UNKNOWN orders, unknown positions, failed restart reconciliation and operator STOP must be visible before ordinary statistics.

## Screens implemented
- Home dashboard
- Executable market opportunities
- Paper positions
- Strategies overview
- Venue health
- Strategy statistics
- Replay
- Diary
- Capital
- Risk Center
- System
- Startup
- DEX Lab
- LIVE controls
- Export Center
- advanced venue/strategy/live/capital renderers ready for callback wiring

## Controls
- Emergency STOP is always reachable from Home.
- Resume is evidence/reconciliation gated.
- Venue REAL toggle is certification + acceptance gated.
- DEX wallet execution remains disabled.
- Export pipeline redacts secret-shaped fields.

## Engine integration added alongside UI
- common Spot/Spot universe builder
- funding-arbitrage service
- per-strategy cycle metrics
- Paper promotion evidence with out-of-sample requirement
- operator event persistence
- audit report bundle
- incident priority ordering

Next: wire venue detail callbacks/toggles, instantiate secondary public clients in main, run Spot/Futures and Spot/Spot loops, and expose their live strategy metrics in this console.
