# Project trading agreement

Read TRADING_MECHANICS_RU.md before modifying strategy or deployment defaults.
The user explicitly requires every strategy to be tested with virtual money
first. Never enable LIVE orders, wallet transactions or transfers from a Paper
test request. Real execution requires separate explicit user authorization
and the existing release acceptance checks.

The current primary PAPER experiment (requested 17 October) is a sequential
spot USDT funding transfer if necessary, BASE buy, BASE transfer, later spot
sale and USDT return. The 16 October seeded-inventory experiment remains
historical and separate. Never claim the initial cross-exchange spread is
locked while a transfer is pending. Earlier futures convergence remains
research-only. A directional CEX position with a DEX price reference is not
the agreed arbitrage mechanism.
Preserve historical losses and monitoring of existing positions on migration.

Latest product request (17 October, 23:18 Asia/Yekaterinburg): retain both
two-leg convergence and sequential buy/transfer/sell. The paired product
entry gate requires an explicit product_dex_paired opt-in, Bybit as the
initial CEX venue, and account-read fee evidence. Historical dex_paired=True
must not silently activate the new product. Opt-in starts in manual PAPER.
Disable-entry and mode switches must preserve monitoring and requested exits.
See TRADING_PRODUCT_RU.md for the complete target and implementation limits.

Latest specification (19 October 2026, 20:59 Asia/Yekaterinburg):
FINAL_SPEC_RU.md is the current two-strategy product contract. A is two-leg
convergence; B is buy/transfer/sell, with prefunded inventory only an execution
variant of B. Preserve all legacy history and monitoring. Never force trades
by inventing identity, fees, fills or liquidity. Record stage results and actual
thresholds, including rejected/expired/channel candidates. Research modules
must not silently become accepted product executors. Read FINAL_SPEC_AUDIT_RU.md
for unaccepted gaps; a running scanner is not release acceptance. LIVE stays off.
