from html import escape
from .tg_format import age


def home(scanner, paper, risk, runtime, live_stop):
    rs = risk.state
    counts = runtime.counts()
    online = sum(x in scanner.clients for x in scanner.ids)
    return (
        "⚡ <b>ARBITRAGE ENGINE</b>\n<i>Рынок · позиции · контроль риска</i>\n\n"
        f"<b>Система</b>\n{'🟢 Работает' if not rs.halted else '🔴 Риск: остановка'} · {'⏸ Сканер на паузе' if scanner.paused else '🟢 Сканер активен'}\n"
        f"Площадки: <b>{online}/{len(scanner.ids)}</b> · цикл {age(scanner.last_scan)}\n\n"
        "<b>Возможности сейчас</b>\n"
        f"Фьючерсы ↔ Фьючерсы: <b>{counts.get('futures_futures',0)}</b>\nСпот ↔ Фьючерсы: <b>{counts.get('spot_futures',0)}</b>\nСпот ↔ Спот: <b>{counts.get('spot_spot',0)}</b>\n\n"
        f"<b>Paper · учебный счёт</b>\nФьючерсных позиций: {len(paper.positions)}/{paper.max_positions}\nNET за день: <b>{rs.paper_daily_pnl:+.4f} USD</b>\n\n"
        "<blockquote>🔐 Реальная торговля заблокирована до проверки исполнения и площадок.</blockquote>\n"
        "<i>Рынок — выбрать возможность. Позиции — следить за выходом. Дневник — разобрать результат.</i>"
    )


def opportunities(rows):
    if not rows:
        return "<b>РЫНОК</b>\n\nПодходящих возможностей сейчас нет."
    out = [
        "<b>РЫНОК · ФЬЮЧЕРСЫ ↔ ФЬЮЧЕРСЫ</b>",
        "<i>Оценки по стаканам, не гарантированная прибыль.</i>",
    ]
    for i, x in enumerate(rows[:8], 1):
        out.append(
            f"<b>{i:02d}  {escape(x['symbol'])}</b>\n{escape(x['buy'])} LONG ↔ {escape(x['sell'])} SHORT\nNET <b>{x['hypothetical_edge']:+.3f}%</b> · вход {x['executable']:.3f}%"
        )
    return "\n\n".join(out)


def venues(scanner):
    h = scanner.health.snapshot()
    out = [
        "🏦 <b>Площадки</b>",
        "<i>Выбери биржу для настройки сканера и Paper.</i>",
        "",
    ]
    for name in scanner.ids:
        x = h.get(name, {})
        out.append(
            f"{'🟢' if name in scanner.clients else '🔴'} <b>{escape(name.upper())}</b> · успешных запросов {x.get('success_pct',0):.0f}% · {escape(str(x.get('latency_ms','—')))} мс"
        )
    out.append("\n<i>Реальная торговля требует отдельной проверки каждой площадки.</i>")
    return "\n".join(out)
