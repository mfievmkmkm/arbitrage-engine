"""Reject scalar, stale, changed-quantity and changed-cost DEX replay marks."""

import math
import re
from decimal import Decimal
from .dex_firm_simulation import integer
from .exchange_executor import SubmitRequest
from .quote_order_evidence import validate as entry_validate
from .recovery_market import validate_evidence as exit_validate
from .public_books import normalize
from .cex_dex_paper_source import calendar


def same(a, b):
    if (
        not math.isfinite(float(a))
        or not math.isfinite(float(b))
        or not math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-10)
    ):
        raise ValueError("DEX_REPLAY_LINEAGE_CONFLICT")


def validate(p, m, ts):
    if (
        m.get("mode") != "DEX_FIRM_PUBLIC_HISTORY_MODEL"
        or m.get("funding_known") is not True
    ):
        raise ValueError("DEX_REPLAY_EVIDENCE_REQUIRED")
    for key in ("entry_dex", "entry_cex", "asset_amount_raw", "cex_contracts"):
        if p[key] != m[key]:
            raise ValueError("DEX_REPLAY_ENTRY_CHANGED")
    for key in ("entry_gas", "entry_cash", "entry_price", "entry_fees", "safety"):
        same(p[key], m[key])
    same(p["base_qty"], p["cex_contracts"] * p["contract_size"])
    entry_validate(
        SubmitRequest(
            p["cex_symbol"],
            p["cex_side"],
            p["cex_contracts"],
            "limit",
            p["entry_price"],
            False,
            True,
            market_evidence=p["entry_cex"],
        ),
        p["cex_venue"],
        p["opened_at"],
    )
    exit_validate(
        SubmitRequest(
            p["cex_symbol"],
            "buy" if p["cex_side"] == "sell" else "sell",
            p["cex_contracts"],
            "market",
            reduce_only=True,
            reference_price=m["exit_price"],
            market_evidence=m["exit_cex"],
        ),
        p["cex_venue"],
        ts,
    )
    for label, at in (("entry", p["opened_at"]), ("exit", ts)):
        q = m[label + "_dex"]
        if (
            q.get("simulation_verified") is not True
            or q.get("ok") is not True
            or q["chain_id"] != p["chain_id"]
            or not re.fullmatch(r"[a-f0-9]{64}", q["quote_fingerprint"])
        ):
            raise ValueError("DEX_REPLAY_CHAIN_PROOF_INVALID")
        if not q["ts"] <= q["received_at"] <= at or not 0 <= at - q["ts"] <= 15:
            raise ValueError("DEX_REPLAY_QUOTE_STALE")
        integer(q["block_number"], "DEX_BLOCK")
        if not re.fullmatch(r"0x[a-fA-F0-9]{64}", q["block_hash"]):
            raise ValueError("DEX_REPLAY_BLOCK_HASH_INVALID")
        forward = p["forward"] if label == "entry" else not p["forward"]
        if (q["sell_token"], q["buy_token"]) != (
            (p["stable"], p["asset"]) if forward else (p["asset"], p["stable"])
        ):
            raise ValueError("DEX_REPLAY_TOKEN_SCOPE_INVALID")
        exact_out = label == "exit" and not p["forward"]
        if q["quote_mode"] != ("exact_out" if exact_out else "exact_in"):
            raise ValueError("DEX_REPLAY_QUOTE_MODE_INVALID")
        base_raw = (
            q["buy_amount_raw"]
            if exact_out
            else q["min_buy_amount_raw"] if forward else q["sell_amount_raw"]
        )
        if str(base_raw) != p["asset_amount_raw"]:
            raise ValueError("DEX_REPLAY_RAW_QUANTITY_INVALID")
        cash_raw = (
            q["max_sell_amount_raw"]
            if exact_out
            else q["sell_amount_raw"] if forward else q["min_buy_amount_raw"]
        )
        same(
            m[label + "_cash"],
            float(
                Decimal(integer(cash_raw, "DEX_CASH"))
                / Decimal(10 ** p["stable_decimals"])
            ),
        )
        b = p["entry_gas_book"] if label == "entry" else m["exit_gas_book"]
        normalize(b, b["symbol"], b["requested_at"], at, 1.5)
        price = p["entry_gas_price"] if label == "entry" else m["exit_gas_price"]
        same(price, b["asks"][0][0])
        same(
            m[label + "_gas"],
            float(
                Decimal(integer(q["network_fee_raw"], "DEX_GAS"))
                / Decimal(10 ** p["native_decimals"])
                * Decimal(str(price))
            ),
        )
    same(
        m["entry_fees"],
        p["base_qty"] * p["entry_price"] * p["fee_rate"] + p["entry_gas"],
    )
    if not 0 <= m["exit_fee_rate"] <= 0.1:
        raise ValueError("DEX_REPLAY_FEE_INVALID")
    same(
        m["exit_fees"],
        p["base_qty"] * m["exit_price"] * m["exit_fee_rate"] + m["exit_gas"],
    )
    sign = 1 if p["forward"] else -1
    same(
        m["gross"],
        sign * (m["exit_cash"] - p["entry_cash"])
        + (1 if p["cex_side"] == "buy" else -1)
        * p["base_qty"]
        * (m["exit_price"] - p["entry_price"]),
    )
    expected = calendar(p, ts)
    events = m["funding_events"]
    if sorted(e["ts"] for e in events) != expected or m["funding_covered_until"] < ts:
        raise ValueError("DEX_REPLAY_FUNDING_INCOMPLETE")
    for e in events:
        if (
            e["venue"] != p["cex_venue"]
            or not math.isfinite(e["rate"])
            or abs(e["rate"]) >= 1
        ):
            raise ValueError("DEX_REPLAY_FUNDING_SCOPE_INVALID")
        same(
            e["amount"],
            (1 if p["cex_side"] == "sell" else -1)
            * e["rate"]
            * p["base_qty"]
            * p["entry_price"],
        )
    same(m["funding"], sum(e["amount"] for e in events))
