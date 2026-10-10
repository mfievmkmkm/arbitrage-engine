from html import escape
from .tg_ui import STRATEGY_LABELS


def render(strategy, x):
    symbol = x.get("symbol") or x.get("base", "—")
    edge = x.get("hypothetical_edge", x.get("net", x.get("carry_pct", 0)))
    out = [
        f"⚡ <b>{escape(symbol)}</b>",
        escape(STRATEGY_LABELS.get(strategy, strategy)),
        f"\nОценка NET / edge: <b>{float(edge):+.3f}%</b>",
    ]
    for k, label in (
        ("executable", "Спред по стаканам"),
        ("funding_pct", "Funding · прогноз"),
        ("fee_pct", "Комиссии · модель"),
        ("safety_pct", "Запас на риск"),
    ):
        if k in x:
            out.append(f"{label}: {float(x[k]):+.3f}%")
    if x.get("buy"):
        out.extend(
            [
                "",
                f"LONG: <b>{escape(x['buy'].upper())}</b>",
                f"SHORT: <b>{escape(x.get('sell','').upper())}</b>",
            ]
        )
    if x.get("exchange"):
        direction = {
            "LONG_SPOT_SHORT_FUTURE": "Спот LONG / Фьючерсы SHORT",
            "LONG_FUTURE_SHORT_SPOT": "Фьючерсы LONG / Спот SHORT · нужен borrowing",
        }.get(x.get("direction"), x.get("direction", "—"))
        out.extend(
            ["", f"Площадка: <b>{escape(x['exchange'].upper())}</b>", escape(direction)]
        )
    if x.get("base_qty") is not None:
        out.append(f"Объём: {x['base_qty']:.8g}")
    if x.get("native_plan"):
        p = x["native_plan"]
        if p.get("valid") is True:
            if strategy == "spot_futures":
                out.append(
                    f"Спот gross: {p['spot']['qty']:.8g} BASE • Фьючерсы: {p['future']['qty']:.8g} контрактов"
                )
                out.append(
                    f"Хедж после резерва base-комиссии: {p['hedged_base']:.8g} BASE\nНезахеджированный остаток: {p['unhedged_base']:.8g} BASE"
                )
            else:
                out.append(
                    "Параметры контракта и округление: проверены по публичным метаданным."
                )
                out.append(
                    f"Контракты LONG / SHORT: {p['long']['qty']:.8g} / {p['short']['qty']:.8g}"
                )
        else:
            out.append(
                "План заявок заблокирован: " + escape(p.get("reason", "UNKNOWN"))
            )
        out.append("Публичный план не подтверждает права аккаунта или допуск к LIVE.")
    out.append(
        "\n<blockquote>Исследовательская оценка. Реальные заявки с этого экрана не отправляются.</blockquote>"
    )
    return "\n".join(out)
