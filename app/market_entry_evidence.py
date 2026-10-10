"""Bounded market entry proof; separate from reduce-only recovery evidence."""

from dataclasses import replace
from .recovery_market import validate_evidence

SOURCE = "PUBLIC_MARKET_ENTRY_V1"


def validate(request, venue, now=None):
    e = request.market_evidence
    if not isinstance(e, dict) or e.get("source") != SOURCE:
        raise ValueError("MARKET_ENTRY_EVIDENCE_REQUIRED")
    if request.reduce_only is not False or e.get("reduce_only") is not False:
        raise ValueError("MARKET_ENTRY_REDUCE_ONLY_INVALID")
    # Use the same depth/time/native-unit/slippage rules as bounded recovery.
    # The persisted source remains entry-specific; no request is mutated.
    validate_evidence(
        replace(request, market_evidence={**e, "source": "PUBLIC_REST_RECOVERY_V1"}),
        venue,
        now,
    )
    return e
