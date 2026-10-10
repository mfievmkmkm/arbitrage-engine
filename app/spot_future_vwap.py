import math


def vwap(levels, qty):
    try:
        qty = float(qty)
        if not math.isfinite(qty) or qty <= 0:
            return None
        rows = [(float(price), float(amount)) for price, amount in levels]
        if any(
            not math.isfinite(price)
            or not math.isfinite(amount)
            or price <= 0
            or amount < 0
            for price, amount in rows
        ):
            return None
        rem = qty
        total = 0
        for price, amount in rows:
            take = min(rem, amount)
            total += take * price
            rem -= take
            if rem <= 1e-12:
                return total / qty
        return None
    except (ValueError, TypeError, OverflowError):
        return None


def executable(spot_book, future_book, base_qty):
    return dict(
        spot_buy=vwap(spot_book["asks"], base_qty),
        spot_sell=vwap(spot_book["bids"], base_qty),
        future_buy=vwap(future_book["asks"], base_qty),
        future_sell=vwap(future_book["bids"], base_qty),
    )
