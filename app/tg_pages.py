def strategies(runtime):
    labels = {
        "futures_futures": "Фьючерсы ↔ Фьючерсы",
        "spot_futures": "Спот ↔ Фьючерсы",
        "spot_spot": "Спот ↔ Спот",
        "funding_arb": "Ставки funding",
        "cex_dex": "CEX ↔ DEX",
    }
    out = ["<b>СТРАТЕГИИ</b>", "Сканер · Paper · реальная торговля", ""]
    for name, label in labels.items():
        enabled = getattr(runtime, "enabled", {}).get(name, False)
        mode = (
            "Paper"
            if name in ("futures_futures", "spot_futures", "spot_spot", "funding_arb")
            else "Исследование"
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
        "⛓ <b>DEX · исследование</b>\n\n"
        "Индикативные котировки 0x — при настройке провайдера и контрактов.\n"
        "Проверки gas, сети, ликвидности и маршрута ещё требуют сквозного подтверждения.\n\n"
        "<blockquote>Paper и реальные on-chain сделки заблокированы.</blockquote>\n"
        "<i>Получение цены не доказывает, что swap исполнится на этих условиях.</i>"
    )
