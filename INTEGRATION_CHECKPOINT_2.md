# INTEGRATION CHECKPOINT 2

Completed in this pass:
- prior integration HEAD verified green in CI;
- public-client factory for strategy-specific market types;
- Spot/Futures service boundary;
- Spot/Spot bounded rotating source service;
- Spot/Futures Paper coordinator;
- Futures/Futures observations now enter unified strategy diary;
- funding interval evidence survives cache hits;
- persistent CSV export service;
- XLSX workbook data model;
- live-position Telegram edit tracker;
- certification-gated venue mode mutation;
- additional integration tests.

Important safety state:
- Futures/Futures remains the only strategy that may eventually receive LIVE authorization.
- Spot/Futures and Spot/Spot are Discovery/Paper only.
- CEX/DEX has no wallet execution adapter.
- AUTO remains locked.
- withdrawals remain outside automatic trading.

Remaining integration before release candidate:
1. instantiate strategy-specific public clients in main lifecycle and close them cleanly;
2. feed Spot/Futures observations + Paper persistence from main cycle;
3. add bounded Spot/Spot universe from common active symbols;
4. persist bankroll/funding attribution from closed trades;
5. implement actual XLSX writer and Telegram export delivery;
6. wire venue mode UI callbacks;
7. wire active live position edit loop from RuntimeStore;
8. add funding-arbitrage observation loop;
9. DEX real quote providers in research/paper;
10. full acceptance + venue certification.
