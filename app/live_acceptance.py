"""Explicit, expiring release evidence. Never derives acceptance from test count."""

import json
import math
import time
from pathlib import Path

CHECKS = (
    "ci",
    "paper",
    "oos",
    "private_streams",
    "withdrawals_disabled",
    "entry_exit_e2e",
    "recovery",
    "restart",
)
VENUE_CHECKS = ("one_way", "ioc", "reduce_only", "client_id", "fees", "funding")
SPOT_CHECKS = (
    "spot_account",
    "spot_ioc",
    "spot_lookup",
    "base_fee_units",
    "spot_balances",
    "cash_entry_exit",
    "cash_recovery",
    "held_inventory",
)
MARKET_CHECKS = (
    "market_entry",
    "market_lookup",
    "market_slippage",
    "zero_fill_transition",
)


def market_fallback_accepted(path, venues, now=None):
    """IOC certification alone never authorizes an unbounded market request."""
    try:
        now = time.time() if now is None else now
        if not accepted(path, venues, now):
            return False
        d = json.loads(Path(path).read_text())
        scoped = d.get("hybrid", {}).get("venues", {})
        # Revalidate the exact second read as well: replacement cannot remove TTL.
        created, expires = d["verified_at"], d["expires_at"]
        if any(
            type(x) not in (int, float) or not math.isfinite(x)
            for x in (created, expires)
        ):
            return False
        if not created <= now < expires or expires - created > 86400:
            return False
        if (
            type(d.get("version")) is not int
            or d["version"] != 1
            or not isinstance(d.get("evidence_id"), str)
            or not d["evidence_id"].strip()
        ):
            return False
        return (
            all(d.get("checks", {}).get(k) is True for k in CHECKS)
            and all(
                all(d.get("venues", {}).get(v, {}).get(k) is True for k in VENUE_CHECKS)
                for v in venues
            )
            and all(
                all(scoped.get(v, {}).get(k) is True for k in MARKET_CHECKS)
                for v in venues
            )
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def accepted(path, venues, now=None, strategy=None):
    try:
        d = json.loads(Path(path).read_text())
        now = time.time() if now is None else now
        if (
            not venues
            or len(set(venues)) != len(venues)
            or isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
        ):
            return False
        if (
            type(d.get("version")) is not int
            or d.get("version") != 1
            or not isinstance(d.get("evidence_id"), str)
            or not d["evidence_id"].strip()
        ):
            return False
        created, expires = d["verified_at"], d["expires_at"]
        if (
            any(
                isinstance(x, bool)
                or not isinstance(x, (int, float))
                or not math.isfinite(x)
                for x in (created, expires)
            )
            or not created <= now < expires
            or expires - created > 86400
        ):
            return False
        standard = all(d.get("checks", {}).get(k) is True for k in CHECKS) and all(
            all(d.get("venues", {}).get(v, {}).get(k) is True for k in VENUE_CHECKS)
            for v in venues
        )
        if strategy is None:
            return standard
        if strategy not in ("spot_futures", "spot_spot", "funding_arb"):
            return False
        if strategy == "spot_futures" and len(venues) != 1:
            return False
        scoped = d.get("strategies", {}).get(strategy, {}).get("venues", {})
        required = (
            SPOT_CHECKS
            if strategy == "spot_futures"
            else (
                SPOT_CHECKS + ("inventory_restoration",)
                if strategy == "spot_spot"
                else (
                    "settlement_calendar",
                    "private_income",
                    "holding_exit",
                    "spread_stop",
                )
            )
        )
        global_checks = all(d.get("checks", {}).get(k) is True for k in CHECKS)
        scoped_checks = all(
            all(scoped.get(v, {}).get(k) is True for k in required) for v in venues
        )
        return (
            global_checks if strategy == "spot_spot" else standard
        ) and scoped_checks
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def spot_accepted(path, venue, now=None):
    return accepted(path, (venue,), now, strategy="spot_futures")


def cash_spot_accepted(path, venue, now=None):
    return accepted(path, (venue,), now, strategy="spot_spot")
