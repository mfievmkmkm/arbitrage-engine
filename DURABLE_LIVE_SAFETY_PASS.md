# DURABLE LIVE SAFETY PASS

This pass first investigated the failed CI run. The failure was a literal escaped newline introduced in funding_arb_service.py; it was corrected before additional work.

Added:
- live_trades write-ahead SQLite lifecycle;
- lifecycle phases PLANNED -> ENTRY_SUBMITTING -> HEDGED_PRIVATE_VERIFIED -> OPEN -> EXIT_SUBMITTING -> CLOSED_PRIVATE_VERIFIED;
- write-ahead Journal facade;
- UNKNOWN submit policy with no blind retry path;
- protective flatten verification requiring trusted private zero exposure;
- persistent STOP that defaults STOPPED on missing/restart state;
- full round-trip NET calculator including entry/exit fees, funding, entry/exit slippage and safety;
- dedicated safety tests.

These modules are foundations for replacing the remaining RuntimeStore-only live lifecycle. They do not authorize LIVE trading.
