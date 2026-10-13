"""Versioned Paper hypothesis, not a profitability certificate or LIVE gate."""
VERSION = 2


def risk_plan(notional, edge):
    # Bound the target instead of demanding 70% of a large, unproven DEX gap.
    stop = max(.10, notional * .0075)
    target = max(.20, notional * .0075)
    return dict(version=VERSION, stop_usd=stop, target_usd=target,
                trail_arm_usd=max(.15, target * .5), trail_fraction=.25,
                min_locked_usd=max(.10, notional * .0025),
                max_seconds=600, progress_seconds=180,
                required_edge=max(target * 2, stop * 2))


def entry_quality(notional, edge, immediate_net):
    plan = risk_plan(notional, edge)
    if edge < plan['required_edge']:
        return 'DEX_V2_REWARD_RISK_LOW'
    if abs(min(0, immediate_net)) > plan['stop_usd'] * .5:
        return 'DEX_V2_EXIT_COST_HIGH'
    if edge > notional * .10:
        return 'DEX_V2_REFERENCE_GAP_EXTREME'
    return None


def exit_reason(position, mark, funding_verified):
    plan = position['reference_policy']
    age = mark['ts'] - position['opened_at']
    price_net = mark['gross'] - mark['entry_fees'] - mark['exit_fees'] - mark['safety']
    # Risk exits never wait for funding certainty.
    if price_net <= -plan['stop_usd']:
        return 'NET_STOP'
    if age >= plan['max_seconds']:
        return 'TIME_STOP'
    if not funding_verified:
        return None
    best, net = position.get('best_net', 0), mark['net']
    if best >= plan['trail_arm_usd'] and net <= max(plan['min_locked_usd'], best * (1-plan['trail_fraction'])):
        return 'PROFIT_GIVEBACK'
    if net >= plan['target_usd']:
        return 'NET_CAPTURE'
    if age >= plan['progress_seconds'] and best < plan['target_usd'] * .25:
        return 'NO_PROGRESS'
    return None


def base_key(route):
    return route.get('base', route.get('symbol', '').split('/')[0]).upper()


def record_close(state, position):
    day = int(position['closed_at'] // 86400)
    key = base_key(position)
    data = state.setdefault('asset_risk', {}).setdefault(key, dict(day=day, losses=0, blocked_until=0))
    if data['day'] != day:
        data.update(day=day, losses=0)
    data['losses'] += int(position['net'] < 0)
    # Applies across chains/contracts of the same CEX asset within this portfolio.
    data['blocked_until'] = max(data['blocked_until'], position['closed_at'] + (3600 if position['net'] < 0 else 600))
    if data['losses'] >= 2:
        data['blocked_until'] = max(data['blocked_until'], (day+1)*86400)


def record_version_stats(state, position):
    version = str(position.get('reference_policy', {}).get('version', 1))
    stats = state.setdefault('policy_stats', {}).setdefault(version, dict(closed=0, wins=0, net=0))
    stats['closed'] += 1
    stats['wins'] += int(position['net'] > 0)
    stats['net'] += position['net']


def blocked_until(state, route):
    return state.get('asset_risk', {}).get(base_key(route), {}).get('blocked_until', 0)
