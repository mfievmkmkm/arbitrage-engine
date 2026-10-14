"""Read-only LIVE checks are deliberately separate from resume/entry actions."""

from html import escape


def render(result):
    out = [
        "🧪 <b>Проверка данных для реального входа</b>",
        "Заявки не отправляются. STOP и настройки запуска не меняются.",
    ]
    if not result:
        return "\n".join(
            out
            + [
                "Нет выбранного маршрута. Дождитесь возможности в разделе «Рынок». Проверка биржевых аккаунтов требует подключённых private API."
            ]
        )
    out.append("Результат: <code>" + escape(str(result["status"])) + "</code>")
    if "symbol" in result:
        out.extend(
            [
                f"<b>{escape(result['symbol'])}</b> • {escape(result['long_venue'])} LONG / {escape(result['short_venue'])} SHORT",
                f"Общий объём: {result['base_qty']:.8g} BASE",
                f"Контракты: LONG {result['long_contracts']:.8g} / SHORT {result['short_contracts']:.8g}",
                f"IOC-лимиты: LONG {result['long_limit']:.8g} / SHORT {result['short_limit']:.8g}",
                f"NET входного спреда после расчётных round-trip комиссий: {result['net_edge_usd']:+.4f} USD",
                f"Требуется с запасом риска/funding: {result['required_net_usd']:.4f} USD",
                f"LIVE-капитал по журналу: {result['equity']:.2f} USD • дневной realized убыток: {result['daily_loss']:.4f} USD",
                "Write-допуск: "
                + (
                    "есть на момент проверки"
                    if result["write_authorized"]
                    else "не предоставлен"
                ),
            ]
        )
    out.append(
        "Это текущий снимок данных и затрат. Он не подтверждает прибыльность или реальное исполнение; отправка заново проверяет допуск и свежие котировки."
    )
    return "\n".join(out)
