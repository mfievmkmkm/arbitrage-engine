"""Historical firm-price attribution, not an extra PnL charge or authority.

Exact-out max input is a protection bound, never the expected execution price.
Legacy quotes without an expected-input reference remain explicitly incomplete.
"""

import hashlib
import json
import math
from decimal import Decimal, localcontext

from .dex_firm_simulation import address, integer


def seal(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def reference(proof, expected_sell, sell_decimals, buy_decimals):
    if expected_sell is None:
        return None
    row = dict(
        version=1,
        source="FIRM_EXPECTED_PRICE_NOT_MIN_MAX_BOUND",
        quote_fingerprint=proof["quote_fingerprint"],
        chain_id=proof["chain_id"],
        sell_token=proof["sell_token"],
        buy_token=proof["buy_token"],
        sell_decimals=sell_decimals,
        buy_decimals=buy_decimals,
        expected_sell_raw=str(integer(expected_sell, "DEX_REFERENCE_SELL")),
        expected_buy_raw=proof["buy_amount_raw"],
        quote_mode=proof["quote_mode"],
        ts=proof["ts"],
        received_at=proof["received_at"],
    )
    return dict(row, sha256=seal(row))


def validate(proof):
    value = proof.get("execution_reference")
    if value is None:
        raise ValueError("DEX_FIRM_EXPECTED_PRICE_MISSING")
    row = dict(value)
    checksum = row.pop("sha256")
    if (
        checksum != seal(row)
        or row.get("version") != 1
        or type(row["version"]) is not int
    ):
        raise ValueError("DEX_REFERENCE_DIGEST_CONFLICT")
    for key in (
        "quote_fingerprint",
        "chain_id",
        "sell_token",
        "buy_token",
        "quote_mode",
        "ts",
        "received_at",
    ):
        if row[key] != proof[key]:
            raise ValueError("DEX_REFERENCE_SCOPE_CONFLICT")
    if (
        row["source"] != "FIRM_EXPECTED_PRICE_NOT_MIN_MAX_BOUND"
        or type(row["chain_id"]) is not int
        or row["chain_id"] != 1
    ):
        raise ValueError("DEX_REFERENCE_SOURCE_INVALID")
    if (
        any(
            type(row[k]) not in (int, float) or not math.isfinite(row[k])
            for k in ("ts", "received_at")
        )
        or not 0 <= row["ts"] <= row["received_at"]
    ):
        raise ValueError("DEX_REFERENCE_TIME_INVALID")
    for token in ("sell_token", "buy_token"):
        if address(row[token]) != row[token]:
            raise ValueError("DEX_REFERENCE_TOKEN_INVALID")
    if row["sell_token"] == row["buy_token"] or any(
        type(row[k]) is not int or not 0 <= row[k] <= 36
        for k in ("sell_decimals", "buy_decimals")
    ):
        raise ValueError("DEX_REFERENCE_UNITS_INVALID")
    sold = integer(row["expected_sell_raw"], "DEX_REFERENCE_SELL")
    bought = integer(row["expected_buy_raw"], "DEX_REFERENCE_BUY")
    if bought != integer(proof["buy_amount_raw"], "DEX_REFERENCE_BUY"):
        raise ValueError("DEX_REFERENCE_AMOUNT_CONFLICT")
    if row["quote_mode"] == "exact_in":
        if sold != integer(proof["sell_amount_raw"], "DEX_REFERENCE_SELL"):
            raise ValueError("DEX_REFERENCE_AMOUNT_CONFLICT")
    elif row["quote_mode"] == "exact_out":
        if sold > integer(proof["max_sell_amount_raw"], "DEX_REFERENCE_MAX"):
            raise ValueError("DEX_REFERENCE_AMOUNT_CONFLICT")
    else:
        raise ValueError("DEX_REFERENCE_MODE_INVALID")
    return row


def attribute(rows, plan):
    results, missing = [], []
    with localcontext() as ctx:
        ctx.prec = 80
        for row in rows:
            payload = json.loads(row["payload"])
            proof, receipt = payload["proof"], payload["receipt"]
            if row["phase"] == "FINALIZED_REVERT":
                if any(int(x) != 0 for x in receipt["token_deltas"].values()):
                    missing.append(
                        dict(
                            intent_id=row["intent_id"],
                            reason="DEX_REFERENCE_REVERT_CASHFLOW_CONFLICT",
                        )
                    )
                    continue
                results.append(
                    dict(
                        intent_id=row["intent_id"],
                        signed_deviation_usdt=0.0,
                        adverse_usdt=0.0,
                        favorable_usdt=0.0,
                        source="REVERT_NO_SWAP",
                    )
                )
                continue
            try:
                ref = validate(proof)
                claimed = row.get("created_at")
                if (
                    type(claimed) not in (int, float)
                    or not math.isfinite(claimed)
                    or not ref["received_at"] <= claimed
                    or claimed - ref["ts"] > 15
                ):
                    raise ValueError("DEX_REFERENCE_NOT_CURRENT_AT_CLAIM")
                units = {
                    plan.asset: plan.asset_decimals,
                    plan.quote: plan.quote_decimals,
                }
                if set((ref["sell_token"], ref["buy_token"])) != set(units) or any(
                    ref[side + "_decimals"] != units[ref[side + "_token"]]
                    for side in ("sell", "buy")
                ):
                    raise ValueError("DEX_REFERENCE_PLAN_UNITS_CONFLICT")
                sold = -int(receipt["token_deltas"][ref["sell_token"]])
                bought = int(receipt["token_deltas"][ref["buy_token"]])
                if sold <= 0 or bought <= 0:
                    raise ValueError("DEX_REFERENCE_ACTUAL_FLOW_INVALID")
                if ref["quote_mode"] == "exact_in" and sold != int(
                    proof["sell_amount_raw"]
                ):
                    raise ValueError("DEX_REFERENCE_ACTUAL_FLOW_INVALID")
                if ref["quote_mode"] == "exact_out" and (
                    bought != int(proof["buy_amount_raw"])
                    or sold > int(proof["max_sell_amount_raw"])
                ):
                    raise ValueError("DEX_REFERENCE_ACTUAL_FLOW_INVALID")
                expected_sell, expected_buy = Decimal(
                    ref["expected_sell_raw"]
                ), Decimal(ref["expected_buy_raw"])
                if ref["sell_token"] == plan.quote:
                    deviation = (
                        Decimal(sold) - Decimal(bought) * expected_sell / expected_buy
                    )
                else:
                    deviation = Decimal(sold) * expected_buy / expected_sell - Decimal(
                        bought
                    )
                deviation /= Decimal(10) ** plan.quote_decimals
                results.append(
                    dict(
                        intent_id=row["intent_id"],
                        signed_deviation_usdt=float(deviation),
                        adverse_usdt=float(max(Decimal(0), deviation)),
                        favorable_usdt=float(max(Decimal(0), -deviation)),
                        source=ref["source"],
                        reference_sha256=proof["execution_reference"]["sha256"],
                    )
                )
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                missing.append(
                    dict(
                        intent_id=row["intent_id"],
                        reason=(
                            str(error)
                            if isinstance(error, ValueError)
                            else "DEX_REFERENCE_INVALID"
                        ),
                    )
                )
    return dict(
        complete=not missing,
        results=results,
        missing=missing,
        adverse_usdt=sum(x["adverse_usdt"] for x in results),
        favorable_usdt=sum(x["favorable_usdt"] for x in results),
    )
