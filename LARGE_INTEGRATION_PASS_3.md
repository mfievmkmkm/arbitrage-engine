# LARGE INTEGRATION PASS 3

Verified before this pass: previous HEAD passed GitHub Actions.

Implemented:
- generic secondary strategy runtime with independent failure isolation;
- Spot/Futures cycle wrapper with Paper coordinator;
- Funding Arbitrage bounded symbol cycle;
- persistent venue controller;
- persistent strategy controller;
- unified Market Console merging strategy opportunities by edge;
- capital router v2 with large reserve and zero DEX live allocation;
- live-card service over RuntimeStore;
- real XLSX writer using openpyxl;
- database export queries and workbook builder;
- critical Telegram alerts;
- professional Risk Center and System screens;
- common Spot/Spot universe support remains available for secondary runtime;
- advanced venue/strategy/capital/live UI from prior pass remains ready for callback wiring.

Safety invariants retained:
- secondary strategy failure cannot crash the primary scanner loop;
- REAL venue mode still requires certification + acceptance;
- CEX/DEX allocation is zero;
- XLSX/exports redact secret-shaped fields;
- UNKNOWN/private/restart state remains fail-closed;
- AUTO remains locked;
- no withdrawal automation.

Next integration:
1. lifecycle-create secondary public clients and SecondaryRuntime from main;
2. use common spot universe to start Spot/Spot service;
3. start Spot/Futures service and restore/persist its Paper positions;
4. build Funding cycle from live FundingService snapshots;
5. wire venue detail callbacks and persistent toggles;
6. wire report generation/download command;
7. wire RuntimeStore active-position cards with periodic edits;
8. add per-strategy promotion pages and Replay evidence;
9. DEX quote provider adapters in Research/Paper;
10. final Stage 5 durability/acceptance audit before any LIVE expansion.
