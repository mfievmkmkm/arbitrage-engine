"""Bounded public rate windows, with explicit calendar and maturity evidence."""

import json
import math
from dataclasses import asdict

import aiosqlite

from .secondary_book_history import spec

SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_rate_windows(id INTEGER PRIMARY KEY,venue TEXT,symbol TEXT,opened_at REAL,covered_until REAL,observed_at REAL,payload TEXT);
CREATE INDEX IF NOT EXISTS funding_rate_window_route ON funding_rate_windows(venue,symbol,opened_at,covered_until);
"""
MODE = "PUBLIC_FUNDING_RATE_WINDOW_MODEL"


class EvidenceError(ValueError):
    def __init__(self, reason, windows):
        super().__init__(reason)
        self.windows = windows


def finite(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError("FUNDING_HISTORY_INVALID")
    return float(value)


def calendar(opened, first, period, until):
    opened, first, period, until = map(finite, (opened, first, period, until))
    if (
        opened < 0
        or until < opened
        or not 0 < period <= 86400
        or not opened < first <= opened + period + 1
    ):
        raise ValueError("FUNDING_CALENDAR_INVALID")
    count = max(0, math.floor((until - first) / period) + 1)
    if count > 100:
        raise ValueError("FUNDING_HISTORY_WINDOW_TOO_LARGE")
    return [first + i * period for i in range(count)]


def validate(window):
    if not isinstance(window, dict) or window.get("mode") != MODE:
        raise ValueError("FUNDING_WINDOW_INVALID")
    instrument = window["instrument"]
    if not isinstance(instrument, dict):
        raise ValueError("FUNDING_INSTRUMENT_INVALID")
    venue, symbol = window["venue"], window["symbol"]
    if (
        not isinstance(venue, str)
        or not venue
        or not isinstance(symbol, str)
        or instrument.get("exchange") != venue
        or instrument.get("symbol") != symbol
        or instrument.get("contract") is not True
        or instrument.get("linear") is not True
        or instrument.get("spot", False) is not False
        or instrument.get("settle") != "USDT"
        or instrument.get("quote") != "USDT"
        or not instrument.get("base")
        or symbol != instrument["base"] + "/USDT:USDT"
        or finite(instrument["contract_size"]) <= 0
    ):
        raise ValueError("FUNDING_INSTRUMENT_INVALID")
    observed, until = finite(window["observed_at"]), finite(window["covered_until"])
    if until > observed - 30:
        raise ValueError("FUNDING_HISTORY_IMMATURE")
    expected = calendar(
        window["opened_at"],
        window["first_settlement"],
        window["interval_seconds"],
        until,
    )
    events = window["events"]
    if not isinstance(events, list) or len(events) != len(expected):
        raise ValueError("FUNDING_SETTLEMENT_MISSING")
    for event, stamp in zip(events, expected):
        if (
            not isinstance(event, dict)
            or finite(event["ts"]) != stamp
            or abs(finite(event["rate"])) >= 1
            or abs(finite(event["reported_ts"]) - stamp) > 1
            or not window["opened_at"] < event["reported_ts"] <= until
        ):
            raise ValueError("FUNDING_HISTORY_INVALID")
    return window


def make_window(position, venue, prefix, rows, market, observed_at):
    instrument = spec(venue, market)
    if instrument is None:
        raise ValueError("FUNDING_INSTRUMENT_INVALID")
    opened = finite(position["opened_at"])
    until = finite(observed_at) - 30
    first = finite(position[prefix + "_next"])
    period = finite(position[prefix + "_interval"]) * 3600
    expected = calendar(opened, first, period, until)
    if not isinstance(rows, list) or len(rows) >= 100:
        raise ValueError("FUNDING_HISTORY_TRUNCATED")
    actual = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("symbol") != position["symbol"]:
            raise ValueError("FUNDING_HISTORY_SYMBOL_MISMATCH")
        stamp, rate = finite(row["timestamp"]) / 1000, finite(row["fundingRate"])
        reported = stamp
        if abs(rate) >= 1:
            raise ValueError("FUNDING_HISTORY_INVALID")
        if not opened < stamp <= until:
            continue
        matches = [ts for ts in expected if abs(stamp - ts) <= 1]
        if len(matches) != 1:
            raise ValueError("FUNDING_CALENDAR_CHANGED")
        stamp = matches[0]
        if stamp in actual and actual[stamp] != (rate, reported):
            raise ValueError("FUNDING_HISTORY_CONFLICT")
        actual[stamp] = (rate, reported)
    if set(actual) != set(expected):
        raise ValueError("FUNDING_SETTLEMENT_MISSING")
    return validate(
        dict(
            mode=MODE,
            venue=venue,
            symbol=position["symbol"],
            opened_at=opened,
            covered_until=until,
            observed_at=observed_at,
            first_settlement=first,
            interval_seconds=period,
            instrument=asdict(instrument),
            events=[
                dict(ts=ts, rate=actual[ts][0], reported_ts=actual[ts][1])
                for ts in expected
            ],
        )
    )


class Store:
    def __init__(self, path, max_rows=20000, retention_seconds=259200):
        if (
            isinstance(max_rows, bool)
            or not isinstance(max_rows, int)
            or max_rows <= 0
            or finite(retention_seconds) <= 0
        ):
            raise ValueError("FUNDING_HISTORY_CONFIG_INVALID")
        self.path, self.max_rows, self.retention = (
            str(path),
            max_rows,
            retention_seconds,
        )
        self.recorded = self.failures = 0

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)

    async def capture(self, position, histories, clients, observed_at):
        # Both venue windows must be verified; no partial pair is written.
        if position["buy"] == position["sell"]:
            raise ValueError("FUNDING_VENUE_PAIR_INVALID")
        windows = [
            make_window(
                position,
                position[key],
                prefix,
                histories[position[key]],
                clients[position[key]].market(position["symbol"]),
                observed_at,
            )
            for key, prefix in (("buy", "long"), ("sell", "short"))
        ]
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            for w in windows:
                await db.execute(
                    "INSERT INTO funding_rate_windows(venue,symbol,opened_at,covered_until,observed_at,payload) VALUES(?,?,?,?,?,?)",
                    (
                        w["venue"],
                        w["symbol"],
                        w["opened_at"],
                        w["covered_until"],
                        w["observed_at"],
                        json.dumps(w, allow_nan=False),
                    ),
                )
            await db.execute(
                "DELETE FROM funding_rate_windows WHERE observed_at<?",
                (observed_at - self.retention,),
            )
            await db.execute(
                "DELETE FROM funding_rate_windows WHERE id IN (SELECT id FROM funding_rate_windows ORDER BY id DESC LIMIT -1 OFFSET ?)",
                (self.max_rows,),
            )
            await db.commit()
        self.recorded += len(windows)
        return len(windows)


class Tape:
    def __init__(self, windows, as_of):
        self.as_of = finite(as_of)
        self.routes = {}
        for w in windows:
            validate(w)
            if w["observed_at"] <= self.as_of:
                self.routes.setdefault((w["venue"], w["symbol"]), []).append(w)

    def covering(self, venue, symbol, start, end, first, period):
        values = self.routes.get((venue, symbol), [])
        eligible = [
            w
            for w in values
            if w["opened_at"] <= start
            and w["covered_until"] >= end
            and w["first_settlement"] == first
            and w["interval_seconds"] == period
        ]
        if not eligible:
            raise ValueError("FUNDING_WINDOW_NOT_COVERED")
        # Check all overlapping evidence, including shorter windows. A newer
        # conflicting rate cannot silently replace a previously recorded rate.
        events, identity = {}, eligible[0]["instrument"]
        witnesses = {}
        for w in values:
            if w["covered_until"] < start or w["opened_at"] > end:
                continue
            if (
                w["instrument"] != identity
                or w["interval_seconds"] != period
                or (w["first_settlement"] - first) % period != 0
            ):
                raise EvidenceError(
                    "FUNDING_HISTORY_IDENTITY_CONFLICT", [eligible[0], w]
                )
            for e in w["events"]:
                if start - 1 <= e["ts"] <= end + 1:
                    value = (e["rate"], e["reported_ts"])
                    if e["ts"] in events and events[e["ts"]] != value:
                        raise EvidenceError(
                            "FUNDING_HISTORY_CONFLICT",
                            [eligible[0], witnesses[e["ts"]], w],
                        )
                    events[e["ts"]] = value
                    witnesses[e["ts"]] = w
        return min(eligible, key=lambda w: (w["observed_at"], w["covered_until"]))
