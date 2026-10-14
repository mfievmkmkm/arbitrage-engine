"""Validated metadata for public cash/linear tapes; never account certification."""

import math
from dataclasses import dataclass
from .instruments import InstrumentSpec, from_market
from .stream_book_recorder import Recorder


@dataclass(frozen=True)
class SpotSpec(InstrumentSpec):
    spot: bool = True


def spec(venue, market):
    if (
        not isinstance(market, dict)
        or not isinstance(market.get("symbol"), str)
        or not market.get("symbol")
        or not isinstance(market.get("base"), str)
        or not market.get("base")
        or market.get("quote") != "USDT"
        or market.get("active") is False
    ):
        raise ValueError("SECONDARY_TAPE_INSTRUMENT_INVALID")
    if market.get("spot") is True:
        if (
            not (market.get("contract") is None or market.get("contract") is False)
            or not (market.get("linear") is None or market.get("linear") is False)
            or market.get("settle") not in (None, "")
            or isinstance(market.get("contractSize"), bool)
            or market.get("contractSize") not in (None, 1)
            or market["symbol"] != market["base"] + "/USDT"
        ):
            raise ValueError("SECONDARY_TAPE_SPOT_SCOPE")
        result = from_market(
            venue, dict(market, contract=False, linear=False, settle="", contractSize=1)
        )
        return SpotSpec(**vars(result))
    size = market.get("contractSize")
    if (
        market.get("contract") is not True
        or market.get("linear") is not True
        or market.get("inverse") is True
        or market.get("settle") != "USDT"
        or isinstance(size, bool)
        or size is None
        or not math.isfinite(float(size))
        or float(size) <= 0
    ):
        raise ValueError("SECONDARY_TAPE_CONTRACT_SCOPE")
    return from_market(venue, market)


def attach(store, clients, interval=1):
    """Attach synchronous offers after clients loaded metadata; no new fetches."""
    specs = {}
    for venue, client in clients.items():
        specs[venue] = {}
        for market in getattr(client, "markets", {}).values():
            try:
                instrument = spec(venue, market)
                specs[venue][instrument.symbol] = instrument
            except (TypeError, ValueError, OverflowError):
                continue
    recorder = Recorder(store, specs, interval=interval, sources=("WS", "REST"))

    def offer(venue, client, book):
        if recorder.closed:
            return
        try:
            symbol = book["symbol"]
            getter = getattr(client, "market", None)
            current = getter(symbol) if callable(getter) else client.markets[symbol]
            if spec(venue, current) != specs[venue][symbol]:
                raise ValueError("SECONDARY_TAPE_METADATA_CHANGED")
        except (KeyError, TypeError, ValueError, OverflowError):
            recorder.dropped += 1
            return
        recorder.offer(venue, book)

    for venue, client in clients.items():
        if callable(getattr(client, "book_status", None)):
            client.on_book = lambda book, venue=venue, client=client: offer(
                venue, client, book
            )
            client.on_snapshot = lambda book, venue=venue, client=client: offer(
                venue, client, book
            )
            client.book_recorder = recorder
    recorder.start()
    return recorder
