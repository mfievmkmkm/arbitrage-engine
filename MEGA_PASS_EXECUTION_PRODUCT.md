# MEGA PASS — EXECUTION SAFETY + PRODUCT INTEGRATION

This pass intentionally batches substantially more work.

CI first:
- previous HEAD failed at compile time because live_trade_store contained literal escaped newline text;
- live_trade_store was rewritten cleanly before expansion.

Execution/durability:
- durable live Session wrapper;
- crash-window classifier;
- deterministic recovery intent factory;
- private-zero verifier;
- unknown-entry reconciliation;
- database-first startup recovery;
- comprehensive micro-live release checklist;
- execution incident model;
- continuous live invariant monitor;
- deterministic live order ID scheme;
- recovery persistence and DB authority from prior pass retained.

Evidence/risk:
- strategy evidence normalization;
- Spot/Futures evidence gate;
- secondary failure isolation policy;
- strategy status matrix;
- fee evidence gate;
- funding evidence gate;
- book freshness evidence gate;
- full round-trip NET proof;
- conservative micro-live budget v2.

Replay/promotion:
- train/out-of-sample metrics;
- profit factor and max drawdown;
- promotion policy v2 requires OOS sample, positive OOS net, OOS PF, DD threshold and zero incidents.

DEX/rebalance:
- provider abstraction and disabled provider;
- research service boundary;
- DEX LIVE hard-locked at authority layer;
- rebalance recommendation planner;
- rebalance execution hard-disabled.

Telegram product:
- Control Console v3;
- Live Detail v2;
- Replay Validation v2;
- Capital Router v2 screen;
- Startup Recovery screen;
- Trade Timeline;
- Incident Center;
- Live Release Gate;
- Execution Health.

Testing:
- broad crash-window tests;
- release checklist tests;
- invariant monitor tests;
- deterministic recovery tests;
- private-zero tests;
- strategy evidence tests;
- OOS promotion tests;
- DEX hard-lock tests;
- rebalance non-execution tests;
- evidence fail-closed tests;
- full NET proof tests;
- micro-live budget tests.

No new strategy receives LIVE authority from this pass.
