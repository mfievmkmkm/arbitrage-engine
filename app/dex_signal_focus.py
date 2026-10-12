"""Explicit Paper signal admission; never executes Telegram prices as fills."""
import json
import os
import re
import time
from pathlib import Path


def enabled(name):
    return os.getenv(name, "false").strip().lower() in ("true", "1", "yes", "on")


def focus_toggles(values):
    return {k: k == "cex_dex" for k in values}


def parse_signal(text):
    direction = re.search(r"\b(LONG|SHORT)\?\s*#([A-Za-z0-9_]+)", text)
    chain = re.search(r"#(SOLANA|ETHEREUM|BASE)\b", text, re.I)
    address = re.search(r"\b0x[a-fA-F0-9]{40}\b", text) if chain and chain[1].upper() != "SOLANA" else re.search(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b", text)
    if not direction or not chain or not address or "Aligned in" in text:
        raise ValueError("Нужен исходный LONG?/SHORT? сигнал с сетью и адресом токена, не отчёт Aligned.")
    return dict(base=direction[2].upper(), side="buy" if direction[1] == "LONG" else "sell",
                chain_id={"SOLANA": "solana", "ETHEREUM": 1, "BASE": 8453}[chain[1].upper()],
                asset=address[0], venue="mexc")


class Signals:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"signals": {}, "blocked": {}}

    @staticmethod
    def key(r):
        asset = r["asset"] if r["chain_id"] == "solana" else r["asset"].lower()
        return f'{r["chain_id"]}:{asset}:{r.get("cex_venue", r.get("venue"))}'

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(self.state), encoding="utf-8")
        temp.replace(self.path)

    def add(self, text, routes, timestamp, origin):
        s = parse_signal(text)
        if not 0 <= self.clock() - timestamp <= 90:
            raise ValueError("Сигнал старше 90 секунд: сохранённые старые цены не используются для входа.")
        matches = [r for r in routes if r["cex_venue"] == s["venue"] and r["base"].upper() == s["base"]
                   and r["chain_id"] == s["chain_id"] and self.key(r) == self.key(s)
                   and r.get("mode") == "reference"]
        if not matches:
            raise ValueError("Нет проверенного MEXC reference-маршрута для этой сети и точного адреса токена.")
        key = self.key(matches[0])
        if timestamp <= self.state["signals"].get(key, {}).get("timestamp", 0):
            raise ValueError("Этот сигнал уже принят или новее него уже есть другой.")
        self.state["signals"][key] = dict(s, timestamp=timestamp, origin=origin)
        self.save()
        return s

    def get(self, r):
        key = self.key(r)
        if self.state["blocked"].get(key, 0) > self.clock():
            return None, "DEX_SIGNAL_LOSS_COOLDOWN"
        signal = self.state["signals"].get(key)
        if not signal or not 0 <= self.clock() - signal["timestamp"] <= 90:
            return None, "DEX_CHANNEL_SIGNAL_REQUIRED"
        return signal, None

    def closed(self, p):
        key = self.key(dict(chain_id=p["chain_id"], asset=p["virtual_route_key"][2], cex_venue=p["cex_venue"]))
        self.state["signals"].pop(key, None)
        if p["net"] < 0:
            self.state["blocked"][key] = self.clock() + 600
        self.save()


def quality(notional, entry_edge, immediate_net, book):
    bid, ask = float(book["bids"][0][0]), float(book["asks"][0][0])
    if bid <= 0 or ask <= bid or (ask - bid) / bid > .0075:
        return "DEX_REFERENCE_BOOK_TOO_WIDE"
    if immediate_net < -notional * .0075:
        return "DEX_REFERENCE_ROUNDTRIP_TOO_COSTLY"
    if entry_edge < max(.10, notional * .01, abs(immediate_net) * 2):
        return "DEX_REFERENCE_REWARD_TOO_SMALL"
    return None
