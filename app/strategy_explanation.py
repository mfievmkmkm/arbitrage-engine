"""Deterministic explanation of income and exits, never an execution authority."""
from .strategy_catalog import CATALOG
ALIASES={'futures_futures':'perp_convergence','spot_futures':'spot_perp','spot_spot':'spot_inventory',
         'funding_arb':'perp_funding','dex_paired':'dex_perp'}


def describe(strategy,position=None):
    if strategy=='spot_spot':
        return ('Старая Spot/Spot модель: обратные сделки и изменение разницы цен после расходов. '
                'Не является новой моделью переводов; новые входы отключены. PAPER, прибыль не гарантирована.')
    key=ALIASES.get(strategy,strategy);spec=CATALOG.get(key)
    if spec is None:
        return 'Источник дохода не определён. Новые входы запрещены.'
    text=f'Как зарабатываем: {spec.income}\nКогда завершаем: {spec.exit}\nУсловия: {spec.requirements}'
    if key in ('perp_convergence','spot_perp','dex_perp','calendar'):
        text+='\nСжатие — сценарий, а не обещание. Спред может расшириться.'
    if key in ('perp_funding','spot_perp','reverse_carry'):
        text+='\nБудущий funding может измениться; прогноз не является прибылью.'
    p=(position or {}).get('paired_policy') or (position or {}).get('plan') or {}
    if p.get('stop_usd') is not None:
        text+=f"\nСтоп NET −${float(p['stop_usd']):.2f}; максимум {float(p['max_seconds']):g} сек."
    return text+'\nPAPER: исполнения и расходы — модель, фактический результат может отличаться.'
