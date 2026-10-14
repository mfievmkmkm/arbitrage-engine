"""Fresh cash IOC proof, with explicit market/account units."""

from dataclasses import replace
from .native_order_plan import number


def validate(request, venue, now=None):
    from .quote_order_evidence import validate as derivative_proof

    e = request.market_evidence
    if (
        not isinstance(e, dict)
        or e.get("source") != "PUBLIC_SPOT_IOC_V1"
        or e.get("market_type") != "spot"
        or number(e.get("contract_size"), "SPOT_UNIT") != 1
        or request.reference_price is not None
    ):
        raise ValueError("SPOT_QUOTE_SCOPE_INVALID")
    # Reuse strict timestamp/depth/limit checks; the original cash proof survives.
    converted = dict(e, source="PUBLIC_IOC_ENTRY_V1")
    derivative_proof(replace(request, market_evidence=converted), venue, now)
    return e
