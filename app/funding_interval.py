import math, re
from dataclasses import dataclass


@dataclass(frozen=True)
class FundingInterval:
    known: bool
    hours: float | None
    reason: str


def infer(row):
    info = row.get("info") or {}
    value = row.get("interval")
    if value is None:
        value = info.get("fundingIntervalHours")
    try:
        if isinstance(value, str):
            match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)\s*", value.lower())
            hours = (
                float(match[1])
                * {"ms": 1 / 3600000, "s": 1 / 3600, "m": 1 / 60, "h": 1, "d": 24}[
                    match[2]
                ]
                if match
                else float(value)
            )
        else:
            hours = float(value)
        if not math.isfinite(hours) or not 0 < hours <= 24:
            return FundingInterval(False, None, "INVALID")
        return FundingInterval(True, hours, "OK")
    except (ValueError, TypeError):
        return FundingInterval(False, None, "UNKNOWN")
