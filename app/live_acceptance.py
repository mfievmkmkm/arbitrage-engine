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


def accepted(path, venues, now=None):
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
        return all(d.get("checks", {}).get(k) is True for k in CHECKS) and all(
            all(d.get("venues", {}).get(v, {}).get(k) is True for k in VENUE_CHECKS)
            for v in venues
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
