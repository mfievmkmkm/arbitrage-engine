"""Exact, currency-aware primitives for the isolated strategy Paper laboratory.

No exchange clients, signing, order submission or implicit currency conversion.
All quantities are BASE; a native lot is contracts_step * contract_multiplier.
Prices and fees must already be expressed in the declared quote currency.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, localcontext
from math import lcm
from functools import wraps

ZERO = Decimal('0')
ONE = Decimal('1')


class Invalid(ValueError):
    pass


def exact(fn):
    @wraps(fn)
    def run(*args,**kwargs):
        with localcontext() as ctx:
            ctx.prec=96
            return fn(*args,**kwargs)
    return run


def D(value):
    if isinstance(value, bool):
        raise Invalid('NUMBER_INVALID')
    try:
        x = Decimal(str(value))
    except Exception as exc:
        raise Invalid('NUMBER_INVALID') from exc
    if not x.is_finite() or len(x.as_tuple().digits) > 128 or abs(x.adjusted()) > 80:
        raise Invalid('NUMBER_INVALID')
    return x


def positive(value):
    x = D(value)
    if x <= 0:
        raise Invalid('POSITIVE_REQUIRED')
    return x


def nonnegative(value):
    x = D(value)
    if x < 0:
        raise Invalid('NEGATIVE_COST')
    return x


def flag(row, key):
    if row.get(key) is not True:
        raise Invalid(key.upper() + '_UNVERIFIED')


def fresh(ts, now, age):
    if not ZERO <= D(now) - D(ts) <= positive(age):
        raise Invalid('QUOTE_STALE')


@exact
def common_quantity(requested, steps):
    """Exact common lattice, including non-power-of-ten contract sizes."""
    steps = [positive(x) for x in steps]
    places = max(0, *(-x.as_tuple().exponent for x in steps))
    scale = Decimal(10) ** places
    lattice = Decimal(lcm(*(int(x * scale) for x in steps))) / scale
    with localcontext() as ctx:
        ctx.prec = 96
        return (positive(requested) / lattice).to_integral_value(rounding=ROUND_DOWN) * lattice


def plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


@dataclass(frozen=True)
class Fill:
    quantity: Decimal
    cash: Decimal
    fee: Decimal
    price: Decimal


class Book:
    def __init__(self, row, now, age=10):
        self.raw = row
        for key in ('id', 'venue', 'base', 'quote', 'kind', 'asset_id', 'quote_asset_id'):
            if not isinstance(row.get(key), str) or not row[key] or len(row[key]) > 150:
                raise Invalid('INSTRUMENT_ID_INVALID')
        flag(row, 'identity_verified')
        fresh(row['ts'], now, age)
        self.id, self.venue = row['id'], row['venue']
        self.base, self.quote, self.asset = row['base'], row['quote'], row['asset_id']
        self.kind = row['kind']
        if self.kind not in ('spot', 'perp', 'future', 'call', 'put', 'dex'):
            raise Invalid('INSTRUMENT_KIND_UNSUPPORTED')
        # Inverse/quanto contracts require another PNL kernel, never a linear shortcut.
        if row.get('linear') is not True or row.get('settle') != self.quote:
            raise Invalid('LINEAR_SETTLEMENT_REQUIRED')
        self.step = positive(row['step']) * positive(row['multiplier'])
        self.minimum = nonnegative(row['min_qty']) * positive(row['multiplier'])
        self.min_cash = nonnegative(row['min_notional'])
        self.rate = nonnegative(row['fee_rate'])
        if row.get('fee_currency')!='quote':
            raise Invalid('QUOTE_DENOMINATED_FEES_REQUIRED')
        if self.rate >= ONE:
            raise Invalid('FEE_RATE_INVALID')
        self.bids = self._levels(row['bids'], True)
        self.asks = self._levels(row['asks'], False)
        if self.bids[0][0] > self.asks[0][0]:
            raise Invalid('BOOK_CROSSED')
        if self.kind == 'dex':
            for key in ('chain', 'contract'):
                if not row.get(key):
                    raise Invalid('TOKEN_IDENTITY_MISSING')
            flag(row, 'token_properties_verified')
            flag(row, 'gas_verified')
            flag(row,'dex_quote_verified')
            positive(row['dex_quote_buy_qty'])
            positive(row['dex_quote_sell_qty'])
            # Initial model supports standard tokens only; taxed/rebasing tokens rejected.
            if D(row['transfer_tax_rate']) != 0 or row.get('rebasing') is not False:
                raise Invalid('TOKEN_MECHANICS_UNSUPPORTED')
            nonnegative(row['gas_entry'])
            nonnegative(row['gas_exit'])

    @staticmethod
    def _levels(rows, reverse):
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
            raise Invalid('BOOK_DEPTH_INVALID')
        out = [(positive(p), positive(q)) for p, q in rows]
        if out != sorted(out, reverse=reverse):
            raise Invalid('BOOK_ORDER_INVALID')
        return out

    @exact
    def fill(self, quantity, side, enforce_lot=True):
        q = positive(quantity)
        if side not in ('buy', 'sell'):
            raise Invalid('SIDE_INVALID')
        if enforce_lot and (q % self.step != 0 or q < self.minimum):
            raise Invalid('NATIVE_LOT_INVALID')
        if self.kind=='dex' and q!=D(self.raw['dex_quote_buy_qty' if side=='buy' else 'dex_quote_sell_qty']):
            raise Invalid('DEX_EXACT_SIZE_QUOTE_REQUIRED')
        remaining, cash = q, ZERO
        for price, depth in self.asks if side == 'buy' else self.bids:
            take = min(remaining, depth)
            cash += take * price
            remaining -= take
            if remaining == 0:
                break
        if remaining:
            raise Invalid('DEPTH_LOW')
        if cash < self.min_cash:
            raise Invalid('MIN_NOTIONAL')
        return Fill(q, cash, cash * self.rate, cash / q)

    @exact
    def buy_for(self, cash):
        """Quote-budgeted BUY; fee is quote-denominated and paid from that budget."""
        budget = positive(cash) / (ONE + self.rate)
        q = ZERO
        for price, depth in self.asks:
            take = min(depth, budget / price)
            q += take
            budget -= take * price
            if budget <= 0:
                break
        q = (q / self.step).to_integral_value(rounding=ROUND_DOWN) * self.step
        return self.fill(q, 'buy')


def leg(book, side, quantity):
    fill = book.fill(quantity, side)
    exit_fill = book.fill(quantity, 'sell' if side == 'buy' else 'buy')
    return dict(instrument=book.id, venue=book.venue, asset_id=book.asset,
                quote=book.quote, quote_asset_id=book.raw['quote_asset_id'], kind=book.kind, side=side, quantity=quantity,
                entry_price=fill.price, entry_cash=fill.cash, entry_fee=fill.fee,
                exit_price=exit_fill.price, exit_cash=exit_fill.cash, exit_fee=exit_fill.fee,
                step=book.step, multiplier=positive(book.raw['multiplier']),
                chain=book.raw.get('chain'), contract=book.raw.get('contract'),
                expiry=book.raw.get('expiry'), strike=book.raw.get('strike'),
                option_style=book.raw.get('option_style'))


@exact
def price_pnl(legs, exits):
    total = ZERO
    for old, new in zip(legs, exits, strict=True):
        q = positive(old['quantity'])
        delta=(D(new['exit_cash'])-D(old['entry_cash']) if 'exit_cash' in new and 'entry_cash' in old
               else q*(D(new['exit_price'])-D(old['entry_price'])))
        total += delta * (
            ONE if old['side'] == 'buy' else -ONE)
    return total
