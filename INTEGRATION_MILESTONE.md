# INTEGRATION MILESTONE

The project has moved from isolated modules toward one operator-facing system.

Connected now:
- Futures/Futures scanner -> StrategyRuntime
- Strategy statistics -> Telegram
- Capital ledger view -> Telegram
- DEX lock/status -> Telegram
- persistent strategy observation schema initialized at startup
- strategy/venue toggle persistence foundations
- Spot/Futures async source + Paper engine + Paper persistence
- Spot/Spot multi-venue source
- funding settlement calendar + paper economics
- persistent attribution ledger
- out-of-sample Replay helper

Next integration sequence:
1. instantiate SpotFutureSource from compatible public clients without interfering with futures-only CCXT clients;
2. feed its observations into strategy_diary;
3. restore/persist Spot/Futures Paper positions;
4. wire Spot/Spot universe scanning in bounded batches;
5. build funding calendar from FundingService snapshots;
6. add CSV/XLSX operator export commands;
7. wire venue Scan/Paper/Real toggles;
8. add active-position edited Telegram messages;
9. implement concrete DEX quote adapters in Research/Paper only;
10. run all strategy acceptance suites before any additional LIVE unlock.
