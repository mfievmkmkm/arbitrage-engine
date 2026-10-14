def strategies(runtime):
    labels = {
        "futures_futures": "Фьючерсы ↔ Фьючерсы",
        "spot_futures": "Спот ↔ Фьючерсы",
        "spot_spot": "Спот ↔ Спот",
        "funding_arb": "Ставки funding",
        "cex_dex": "CEX ↔ DEX",
    }
    paired = 'dex_paired' in getattr(runtime, 'enabled', {})
    if paired:
        labels.pop('cex_dex')
        labels.update(dex_paired='DEX BUY ↔ CEX SHORT · парный тест')
    elif "dex_channel" in getattr(runtime, "enabled", {}):
        labels.update(dex_channel="Канал → MEXC · независимый Paper", dex_bybit="Автопоиск → Bybit · независимый Paper")
    out = ["<b>СТРАТЕГИИ · ВИРТУАЛЬНЫЕ ДЕНЬГИ</b>" if paired else "<b>СТРАТЕГИИ</b>",
           "Две встречные позиции · результат после расходов · LIVE выключен" if paired else "Сканер · Paper · реальная торговля", ""]
    for name, label in labels.items():
        enabled = getattr(runtime, "enabled", {}).get(name, False)
        mode = (
            "Paper"
            if name in ("futures_futures", "spot_futures", "spot_spot", "funding_arb", "dex_channel", "dex_bybit", "dex_paired")
            else "Firm Paper / исследование"
        )
        out.append(
            f"<b>{label}</b>\n{'🟢 Включена' if enabled else '⚫ Выключена'} • {mode} • REAL 🔒\nНаблюдений сейчас: {runtime.counts().get(name,0)}"
        )
    out.append(
        "\nОтключение запрещает новые входы; открытые Paper-позиции продолжают наблюдаться."
    )
    return "\n\n".join(out)


def dex():
    return (
        "⛓ <b>DEX · котировки и Paper</b>\n\n"
        "Price-research даёт индикативную цену. Firm Paper требует проверенных контрактов, RPC, существующего инвентаря кошелька и комиссии CEX.\n"
        "Вход и обратный выход моделируются отдельно; газ и история funding входят в NET.\n\n"
        "<i>Котировки и eth_call не являются исполненными сделками. Реальные транзакции кошелька пока не подключены.</i>"
    )
