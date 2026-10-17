# Project trading agreement

Read TRADING_MECHANICS_RU.md before modifying strategy or deployment defaults.
The user explicitly requires every strategy to be tested with virtual money
first. Never enable LIVE orders, wallet transactions or transfers from a Paper
test request. Real execution requires separate explicit user authorization
and the existing release acceptance checks.

The current primary PAPER experiment is spot inventory with two equal-BASE
fills and explicit transfer rebalancing (approved 16 October). Do not replace
it with spread convergence. Earlier futures convergence remains research-only. A directional CEX position with a DEX price
reference is not a replacement for the agreed arbitrage mechanism.
Preserve historical losses and monitoring of existing positions on migration.
