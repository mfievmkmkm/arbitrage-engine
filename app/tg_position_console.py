import time
from html import escape


def paper(paper, sf_paper=None):
    rows = list(paper.positions.values())
    sf = list(sf_paper.positions.values()) if sf_paper else []
    out = [
        "📈 <b>Позиции · Paper</b>",
        "<i>Оценка закрытия по наблюдаемым ценам.</i>",
        f"\nФьючерсы ↔ Фьючерсы: <b>{len(rows)}</b>\nСпот ↔ Фьючерсы: <b>{len(sf)}</b>",
    ]
    if not rows and not sf:
        out.append("\nОткрытых позиций нет. Сканер продолжает поиск.")
    for p in rows[:5]:
        out.append(
            f"\n<b>{escape(p.symbol)}</b>\n{escape(p.buy.upper())} LONG ↔ {escape(p.sell.upper())} SHORT\nСпред: {p.entry_spread:.3f}% → {p.current_spread:.3f}%\nОценка выхода: <b>{p.current_net_usd:+.4f} USD</b>\nЛучший NET: {p.best_net_usd:+.4f} USD"
        )
    for p in sf[:5]:
        stamp = (p.last_mark or {}).get("ts")
        fresh = stamp is not None and 0 <= time.time() - stamp <= 30
        out.append(
            f"\n<b>{escape(p.base)}</b> · {escape(p.exchange.upper())}\nСпот LONG ↔ Фьючерсы SHORT\nОбъём: {p.base_qty:.8g}\nОценка выхода: <b>{p.net:+.4f} USD</b>\n{'🟢 Свежая оценка' if fresh else '🟠 Оценка устарела или ещё не получена'}"
        )
    if len(rows) > 5 or len(sf) > 5:
        out.append(
            "\nПоказаны первые 5 позиций каждой стратегии; полная история — в выгрузке."
        )
    out.append(
        "\n<i>Комиссии Paper модельные; прогноз funding не считается заработанным.</i>"
    )
    return "\n".join(out)
