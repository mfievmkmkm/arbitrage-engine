MAP = {
    "open": "ACK",
    "new": "ACK",
    "closed": "FILLED",
    "filled": "FILLED",
    "canceled": "CANCELED",
    "cancelled": "CANCELED",
    "rejected": "REJECTED",
    "expired": "CANCELED",
}


def normalize(status, filled=0, amount=None):
    s = str(status or "").lower()
    if s in ("canceled", "cancelled", "rejected", "expired"):
        return MAP[s]
    if amount is not None and amount > 0:
        if filled >= amount * (1 - 1e-10):
            return "FILLED"
        if filled > 0:
            return "PARTIAL"
    return MAP.get(s, "UNKNOWN")
