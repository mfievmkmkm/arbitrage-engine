from dataclasses import dataclass


@dataclass(frozen=True)
class Gate:
    scan: bool
    paper: bool
    live: bool
    reason: str


def evaluate(
    strategy, campaign_ready, dedicated_acceptance=False, cash_account_acceptance=False
):
    if strategy == "futures_futures":
        return Gate(
            True,
            True,
            dedicated_acceptance,
            "LIVE_ACCEPTANCE" if dedicated_acceptance else "LIVE_LOCKED",
        )
    if strategy == "spot_futures":
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
    return Gate(False, False, False, "DEDICATED_DEX_ACCEPTANCE_REQUIRED")
