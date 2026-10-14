# Project trading agreement

Read TRADING_MECHANICS_RU.md before modifying strategy or deployment defaults.
The user explicitly requires every strategy to be tested with virtual money
first. Never enable LIVE orders, wallet transactions or transfers from a Paper
test request. Real execution requires separate explicit user authorization
and the existing release acceptance checks.

The primary mechanism is two equal-base opposite legs and net spread
convergence after all costs. A directional CEX position with a DEX price
reference is not a replacement for the agreed arbitrage mechanism.
Preserve historical losses and monitoring of existing positions on migration.
