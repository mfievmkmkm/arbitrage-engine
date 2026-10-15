"""Two-leg Paper economics. Forecast income is never realized profit."""
import math


def plan(notional, raw_edge, costs, funding_reserve=0):
    values = (notional, raw_edge, costs, funding_reserve)
    if not all(math.isfinite(float(x)) for x in values) or notional <= 0 or min(costs, funding_reserve) < 0:
        raise ValueError('PAIRED_ECONOMICS_INVALID')
    # Admit on a partial convergence scenario, not on a promise of zero spread.
    partial_net = raw_edge * .5 - costs - funding_reserve
    if raw_edge <= 0 or partial_net < .25:
        raise ValueError('PAIRED_PARTIAL_NET_LOW')
    if raw_edge > notional * .10:
        raise ValueError('PAIRED_REFERENCE_GAP_EXTREME')
    return dict(version=1, mechanism='TWO_LEG_CONVERGENCE',
                partial_net=partial_net, raw_edge=raw_edge, costs=costs,
                funding_reserve=funding_reserve, target_usd=partial_net,
                stop_usd=max(.50, notional * .01),
                trail_arm_usd=max(.25, partial_net * .5),
                max_seconds=1200)


def exit_reason(policy, opened_at, best, mark, funding_known):
    price_net = mark['gross'] - mark['entry_fees'] - mark['exit_fees'] - mark['safety']
    # Settled funding is real model PnL, not a forecast. It must participate in
    # the loss limit. Without verified funding retain the price-only emergency
    # exit; accounting stays pending rather than inventing a zero payment.
    risk_net = min(price_net, mark['net']) if funding_known else price_net
    if risk_net <= -policy['stop_usd']:
        return 'NET_STOP'
    if mark['ts'] - opened_at >= policy['max_seconds']:
        return 'TIME_STOP'
    if not funding_known:
        return None
    net = mark['net']
    if net >= policy['target_usd']:
        return 'PARTIAL_CONVERGENCE_NET'
    if best >= policy['trail_arm_usd'] and net <= max(.10, best * .75):
        return 'PROFIT_GIVEBACK'
    return None


def futures_plan(row):
    if row.get('funding_known') is not True or not row.get('funding_calendar'):
        raise ValueError('PAIRED_FUNDING_CALENDAR_UNKNOWN')
    qty = row['base_qty']
    raw = qty * (row['entry_sell'] - row['entry_buy'])
    fees = row.get('entry_fee_usd', qty * (row['entry_buy'] + row['entry_sell']) * row['fee_pct'] / 400) + row.get('exit_fee_usd', qty * (row['exit_buy'] + row['exit_sell']) * row['fee_pct'] / 400)
    safety = row['notional'] * row.get('safety_pct', 0) / 100
    spread_cost = qty * ((row['entry_buy'] - row['exit_buy']) + (row['exit_sell'] - row['entry_sell']))
    # Include visible bid/ask friction when assessing the partial convergence.
    result = plan(row['notional'], raw, fees + safety + max(0, spread_cost), row.get('funding_reserve_usd', 0))
    if spread_cost + fees + safety > result['stop_usd'] * .5:
        raise ValueError('PAIRED_IMMEDIATE_COST_HIGH')
    return result
