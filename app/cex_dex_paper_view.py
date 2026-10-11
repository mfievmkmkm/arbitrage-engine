from html import escape


def render(engine):
    out = [
        "\n\n⛓ <b>CEX ↔ DEX · Paper</b>",
        "<i>Отдельные firm-котировки входа и выхода. Min-out / max-in, газ, комиссии и история funding — модель, не сделки кошелька.</i>",
    ]
    if engine is None:
        return "\n".join(
            out
            + [
                "Нужны настроенные маршруты, проверенный реестр токенов, RPC и аккаунтные комиссии CEX."
            ]
        )
    if getattr(engine, "virtual", False):
        out[1] = "<i>Виртуальный капитал. Индикативные котировки 0x / Jupiter и стаканы CEX; расходы и funding учитываются. Баланс и разрешения настоящего кошелька не требуются. Это модель исполнения, не подтверждение исполнимости.</i>"
        out.append(f"Капитал модели: {engine.budget():.2f} USD · свободно после общих резервов: {max(0, engine.budget() - engine.used_capital - engine.external_reserved()):.2f} USD")
        out.append(f"Закрыто виртуальных: {engine.state.get('closed_count', 0)} · итоговый NET: {engine.state.get('realized_net', 0):+.4f} USD")
        out.append("Эта история выгружается отдельно и не подтверждает готовность LIVE. Парный режим: виртуальная покупка DEX + шорт CEX. Reference: направленный LONG/SHORT CEX по ориентиру DEX, без хеджа.")
        routes = getattr(getattr(engine, "source", None), "routes", [])
        out.append(f"Маршрутов: {len(routes)} · проверок входа: {engine.state.get('checked_count', 0)}")
        last = engine.state.get("last_route", {})
        if last:
            age = max(0, engine.clock() - last["ts"])
            out.append(f"Последняя проверка: {escape(last['label'])} · {age:.0f} с назад")
        counts = engine.state.get("decision_counts", {})
        if counts:
            out.append("Причины входов/пропусков: " + " · ".join(f"{escape(k)}: {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])[:3]))
    out.append(
        f"Резерв: {engine.used_capital:.2f} USD · позиций: {len(engine.positions)}"
    )
    if engine.state.get("last_decision"):
        out.append("Последний вход: " + escape(engine.state["last_decision"]))
    detail = engine.state.get("last_detail", {})
    if detail:
        out.append(f"Режим: {escape(detail['virtual_mode'])} · {escape(detail['cex_side'])}\nПотенциал NET при схождении: {detail['entry_edge']:+.4f} USD · это не заработанный PNL\nКомиссии входа модели: {detail['entry_fees']:.4f} USD · {escape(detail['fee_source'])}")
    for p in engine.positions.values():
        direction = ('CEX LONG' if p['cex_side'] == 'buy' else 'CEX SHORT') + ' · DEX только ориентир' if p.get('virtual_mode') == 'reference' else ('DEX BUY / CEX SHORT' if p['forward'] else 'DEX SELL / CEX LONG')
        out.append(
            f"\n<b>{escape(p['symbol'])}</b> · {escape(p['cex_venue'])}\n{direction}\nNET модели: {p['net']:+.4f} USD\n{escape(p['status'])} · {escape(p.get('funding_reason', 'история ещё не сверена'))}"
        )
        if p.get("data_reason", "OK") != "OK":
            out.append("Данные выхода: " + escape(p["data_reason"]))
    out.append(
        "\nПрогноз funding не зачисляется. Неполные данные удерживают позицию и резерв."
    )
    return "\n".join(out)
