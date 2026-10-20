"""Sequential spot BUY -> transfer -> SELL. Future bids are scenarios only."""
from .strategy_math import D, ONE, ZERO, plain, positive, nonnegative, common_quantity
from .inventory_paper import fill
from .evidence_error import EvidenceError


def limits(market,qty,cost):
    rows=market.get('limits') or {}
    for key,value in (('amount',D(qty)),('cost',D(cost))):
        spec=rows.get(key) or {}
        if spec.get('min') is None:
            raise EvidenceError('Неизвестен минимум '+key,observed=str(value),minimum=None,unit=key,symbol=market.get('symbol'))
        if value<D(spec['min']) or (spec.get('max') is not None and value>D(spec['max'])):
            raise EvidenceError('Размер ордера за пределами '+key,observed=str(value),
                                minimum=str(spec['min']),maximum=spec.get('max'),unit=key,symbol=market.get('symbol'))


def deposit_known(route):
    return route.get('deposit_fee') is not None and route.get('deposit_fee_percentage') is False


def plan(a,b,base_route,quote_route,budget=25,capital=250,min_spread=5,max_spread=10,min_net='.25',stress_drop=3):
    budget=min(positive(budget),positive(capital));safety=D('.10')
    min_spread,max_spread,min_net,stress_drop=(positive(min_spread),positive(max_spread),positive(min_net),positive(stress_drop))
    if min_spread>max_spread or max_spread>50 or stress_drop>20:raise ValueError('Неверные лимиты стратегии')
    q=common_quantity(budget/(positive(a['asks'][0][0])*(ONE+nonnegative(a['fee_rate']))),[a['step']])
    bought,cost,buy_fee=fill(a,q,'buy')
    if bought!=q:
        raise ValueError('Недостаточная глубина покупки')
    withdraw_fee=nonnegative(base_route['fee'])
    receipt=q-withdraw_fee
    if q<nonnegative(base_route['withdraw_minimum']) or receipt<nonnegative(base_route['deposit_minimum']) or receipt<=ZERO:
        raise ValueError('BASE меньше комиссии или минимумов перевода')
    reasons=[]
    for name,r in (('BASE',base_route),('USDT',quote_route)):
        if not deposit_known(r):
            reasons.append(name+': не подтверждена фиксированная комиссия депозита')
        if r.get('deposit_fee') is not None and nonnegative(r['deposit_fee'])!=0:
            reasons.append(name+': ненулевая комиссия депозита пока не поддерживается')
    sell_qty=common_quantity(receipt,[b['step']])
    if sell_qty<=0:
        raise ValueError('После перевода меньше шага продажи')
    sold,cash,sell_fee=fill(b,sell_qty,'sell')
    if sold!=sell_qty:
        raise ValueError('Недостаточная глубина продажи')
    return_fee=nonnegative(quote_route['fee'])
    proceeds=cash-sell_fee
    if proceeds<nonnegative(quote_route['withdraw_minimum']) or proceeds-return_fee<nonnegative(quote_route['deposit_minimum']):
        reasons.append('USDT меньше минимумов обратного перевода')
    spent=cost+buy_fee
    net=proceeds-return_fee-spent
    scenarios=[]
    for drop in (0,1,3,5,10):
        scenarios.append(dict(drop_pct=drop,net=str(cash*(ONE-D(drop)/100)*(ONE-D(b['fee_rate']))-return_fee-spent-safety)))
    break_even=(spent+return_fee+safety)/(sell_qty*(ONE-nonnegative(b['fee_rate'])))
    spread=(cash/sell_qty/(cost/q)-ONE)*100
    if spread<min_spread:reasons.append('Спред меньше заданного минимума')
    if spread>max_spread:reasons.append('Спред выше максимума: аномалия')
    if net-safety<min_net:reasons.append('NET после резерва ниже установленного минимума')
    stressed=cash*(ONE-stress_drop/100)*(ONE-D(b['fee_rate']))-return_fee-spent-safety
    if stressed<min_net:reasons.append('Падение цены за время перевода '+str(stress_drop)+'% уничтожает минимальный NET')
    return plain(dict(buy_qty=q,sell_qty=sell_qty,received_qty=receipt,dust_qty=receipt-sell_qty,
        buy_cash=cost,buy_fee=buy_fee,sell_cash=cash,sell_fee=sell_fee,spent=spent,
        return_fee=return_fee,net_now=net,net_after_reserve=net-safety,safety=safety,
        buy_vwap=cost/q,sell_vwap=cash/sell_qty,break_even_sell=break_even,
        price_drop_to_break_even_pct=(ONE-break_even/(cash/sell_qty))*100,
        spread_pct=spread,scenarios=scenarios,stress_drop_pct=stress_drop,stress_net=stressed,reasons=reasons,
        base_route=base_route,quote_route=quote_route,
        delay_evidence='UNMEASURED_900S_PER_LEG_PAPER_ASSUMPTION'))
