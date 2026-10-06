# LARGE INTEGRATION PASS 4

Previous HEAD was verified green before this pass.

Major work:
- secondary strategy bootstrap bundle for public spot/swap clients;
- Spot/Futures cycle + Paper coordinator;
- Spot/Spot service bootstrap from common universe;
- secondary runtime lifecycle abstraction;
- interactive Market detail renderer and opportunity keyboard;
- venue control keyboards;
- combined Futures/Futures + Spot/Futures Paper position console;
- operator audit helper;
- hard strategy LIVE policy: only Futures/Futures is currently eligible;
- periodic LIVE card loop over RuntimeStore;
- Telegram report delivery;
- promotion gate screen;
- persistent venue certification evidence store;
- funding unit normalization from CCXT fractional rate to percent;
- funding arbitrage economics now subtracts round-trip fees, basis risk and safety;
- Spot/Spot reverse-direction bug fixed: both directions now use VWAP;
- main Telegram market screen now has ranked opportunity buttons;
- positions screen now uses the combined console.

Still deliberately locked:
- secondary bootstrap exists but is not yet invoked from main until its startup failure behavior and client type interactions receive integration tests;
- Spot/Futures funding remains discovery-unsafe until FundingService is injected into that source;
- DEX has no provider/wallet execution;
- LIVE authorization remains Futures/Futures only;
- AUTO remains locked.
