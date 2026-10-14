# DURABILITY PASS 2

The previous CI run was inspected first: 340 tests passed and one Spot/Spot source test failed because the source still called the removed evaluate symbol. The source was corrected to call the new bidirectional VWAP evaluator.

Added in this pass:
- persisted recovery order helper using deterministic OrderIntent IDs;
- recovery rounder selection by actual target venue;
- database-first restart authority;
- DB-vs-Runtime reconciliation planner;
- write-ahead entry safety wrapper;
- write-ahead close safety wrapper;
- explicit private-state failure action;
- durable live trade lookup;
- five new durability test modules.

Restart invariant:
If RuntimeStore contains a trade absent from durable DB, halt.
If durable DB contains active exposure absent from RuntimeStore, private reconcile.
If order intents are UNKNOWN, halt.
If active durable trades exist while private state is untrusted, halt.

Recovery invariant:
Recovery orders use deterministic persisted intents and the rounding function belonging to the venue actually receiving the recovery order.

No LIVE authorization was expanded.
