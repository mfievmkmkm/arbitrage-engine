"""All playbook mechanisms; deployment capability is deliberately separate."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Spec:
    key: str
    title: str
    family: str
    source: str
    income: str
    exit: str
    requirements: str


ROWS = (
 ('spot_inventory', 'Spot/Spot · запасы', 'inventory', 'legacy_spot_spot',
  'Исполненная разница покупки/продажи минус ребаланс.', 'Подтверждён полный ребаланс.', 'Деньги и монеты на разных площадках.'),
 ('spot_transfer', 'Spot/Spot · перевод', 'observe', 'evidence',
  'Цена продажи после доставки неизвестна.', 'Наблюдение, автоматический вход запрещён.', 'Вывод, сеть, депозит и время доставки.'),
 ('perp_convergence', 'Фьючерсы · схождение', 'pair', 'legacy_futures_futures',
  'Сжатие спреда равнобазовой LONG/SHORT пары.', 'NET, возврат прибыли, стоп или время.', 'Два линейных perpetual одного актива.'),
 ('perp_funding', 'Фьючерсы · funding', 'carry', 'legacy_funding_arb',
  'Разница подтверждённых выплат двух площадок.', 'NET, ухудшение carry, стоп или горизонт.', 'Календарь событий и обе маржи.'),
 ('spot_perp', 'Спот / Фьючерсы', 'pair', 'legacy_spot_futures',
  'Сжатие basis и/или подтверждённый funding.', 'Две ноги по NET, риску или горизонту.', 'Полная покупка spot и маржа SHORT.'),
 ('reverse_carry', 'Обратный carry · заём', 'carry', 'evidence',
  'Отрицательный basis/carry после процентов займа.', 'Выкуп spot, закрытие LONG и возврат займа.', 'Подтверждённый заём с лимитом и стоимостью.'),
 ('dated_carry', 'Спот / срочный futures', 'expiry', 'evidence',
  'Премия контракта за вычетом расходов срока.', 'Ранний NET или проверенный settlement.', 'Expiry, индекс и план продажи spot.'),
 ('calendar', 'Календарный спред', 'expiry', 'evidence',
  'Изменение относительной цены двух сроков.', 'Цель модели, стоп или до ближней expiry.', 'Два срока и обоснованный целевой спред.'),
 ('cex_dex_spot', 'CEX spot / DEX spot', 'inventory', 'evidence',
  'Разница двух исполненных spot-обменов.', 'Сверка fills и последующего ребаланса.', 'Точная identity и запас токена/денег.'),
 ('dex_perp', 'DEX BUY / CEX SHORT', 'pair', 'legacy_dex_paired',
  'Сжатие цены токена и perpetual.', 'DEX SELL + закрытие SHORT, полный NET.', 'Сеть/контракт, gas, quote и равный BASE.'),
 ('dex_atomic', 'DEX/DEX · атомарно', 'cycle', 'evidence',
  'Прирост исходного актива внутри транзакции.', 'Receipt, баланс и gas; revert тоже расход.', 'Одна сеть, полный маршрут и симуляция.'),
 ('cross_chain', 'DEX · разные сети', 'inventory', 'evidence',
  'Разница цен при размещённых заранее запасах.', 'Два swaps плюс отдельный ребаланс.', 'Проверенная эквивалентность токенов и finality.'),
 ('triangle', 'Треугольный цикл', 'cycle', 'evidence',
  'Прирост исходной валюты после всех шагов.', 'Возврат в исходную валюту и учёт остатков.', 'Глубина/лот каждого шага и аварийный выход.'),
 ('options_parity', 'Опционы · conversion', 'options', 'evidence',
  'Относительная цена spot + put − call.', 'Обратные fills либо проверенный расчёт.', 'Линейные European, одинаковые strike/expiry.'),
 ('stable_regional', 'Stable / региональные', 'cycle', 'evidence',
  'Исполнимый замкнутый обмен; региональные — наблюдение.', 'Возврат в исходную валюту без допущения peg=$1.', 'Точные валюты и доступный расчётный путь.'),
)
CATALOG = {row[0]: Spec(*row) for row in ROWS}
