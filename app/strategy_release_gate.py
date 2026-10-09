from dataclasses import dataclass


@dataclass(frozen=True)
class Gate:
    scan: bool
    paper: bool
    live: bool
    reason: str


def evaluate(
    strategy,
    campaign_ready,
    dedicated_acceptance=False,
    cash_account_acceptance=False,
    funding_account_acceptance=False,
):
    if strategy == "futures_futures":
        return Gate(
            True,
            True,
            dedicated_acceptance,
            "LIVE_ACCEPTANCE" if dedicated_acceptance else "LIVE_LOCKED",
        )
    if strategy in ("spot_futures", "spot_spot"):
        live = dedicated_acceptance is True and cash_account_acceptance is True
        return Gate(
            True,
            True,
            live,
            (
                "CASH_ACCOUNT_ACCEPTANCE"
                if live
                else ("PAPER_ONLY" if not campaign_ready else "SEMI_AUTO_REVIEW")
            ),
        )
    if strategy == "funding_arb":
        live = dedicated_acceptance is True and funding_account_acceptance is True
        return Gate(
            True,
            True,
            live,
            "FUNDING_ACCOUNT_ACCEPTANCE" if live else "FUNDING_ACCEPTANCE_REQUIRED",
        )
    if strategy == "cex_dex":
        return Gate(True, True, False, "DEX_LIVE_COORDINATOR_AND_ACCEPTANCE_REQUIRED")
    return Gate(False, False, False, "UNKNOWN_STRATEGY")
