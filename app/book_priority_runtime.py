"""Public transport demand from scanner and durable exposure, without write gates."""

import json


def publish(clients, owner, required=None, candidates=None, priority=1):
    required, candidates = required or {}, candidates or {}
    for venue, client in clients.items():
        method = getattr(client, "set_book_priorities", None)
        if callable(method):
            method(
                owner,
                list(dict.fromkeys(required.get(venue, ()))),
                list(dict.fromkeys(candidates.get(venue, ()))),
                priority=priority,
            )


def live(rows, derivative_clients, cash_clients):
    derivative, cash = {}, {}

    def add(target, venue, symbol):
        if isinstance(venue, str) and isinstance(symbol, str) and venue and symbol:
            target.setdefault(venue, []).append(symbol)

    for row in rows:
        meta = json.loads(row["payload"])
        strategy = meta.get("strategy", "futures_futures")
        if strategy == "spot_futures":
            p = meta["cash_plan"]
            add(derivative, p["venue"], p["future_symbol"])
            add(cash, p["venue"], p["spot_symbol"])
            add(cash, p["venue"], p["future_symbol"])
        elif strategy == "spot_spot":
            p = meta["spot_spot_plan"]
            for venue in p["venues"]:
                add(cash, venue, p["symbol"])
        elif strategy == "cex_dex":
            p = meta["dex_live_plan"]
            add(derivative, p["venue"], p["symbol"])
            add(cash, p["venue"], p["symbol"])
            add(cash, p["venue"], "ETH/USDT")
        else:
            for venue in (row["long_venue"], row["short_venue"]):
                add(derivative, venue, row["symbol"])
    # Publish only after all durable payloads were parsed successfully.
    publish(derivative_clients, "live_derivative", derivative, priority=0)
    publish(cash_clients, "live_cash", cash, priority=0)
