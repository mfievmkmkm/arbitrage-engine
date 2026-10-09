from html import escape
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button


def render(summary, coordinator, configured=False, preview=None):
    out = [
        "📊 <b>Spot/Futures LIVE</b>",
        "Вход: "
        + (
            "настроен; требует STOP/release/account допуска"
            if configured
            else "не настроен"
        ),
        "Сначала покупка спота, затем хедж фактически полученного актива.",
    ]
    if coordinator:
        out.append(
            "Последняя проверка: <code>"
            + escape(str(coordinator.latest.get("status", "—")))
            + "</code>"
        )
    if preview:
        out.append(
            "\nПроверка без заявок: <code>"
            + escape(str(preview.get("status", "—")))
            + "</code>"
        )
        if preview.get("reason"):
            out.append(escape(str(preview["reason"])))
    trades = [
        x
        for x in (summary or {}).get("trades", [])
        if x.get("strategy") == "spot_futures"
    ]
    if not trades:
        out.append("\nАктивных Spot/Futures циклов нет.")
    for x in trades:
        out.append(
            f"\n<b>{escape(x.get('spot_symbol', x['symbol']))}</b>\n<code>{escape(x['trade_id'])}</code>\nЭтап: <code>{escape(x['phase'])}</code>\nPrivate: {'сверено' if x.get('private_verified') else 'не подтверждено'}"
        )
        flow = x.get("cashflow")
        if flow:
            out.append(
                f"Принадлежит циклу: спот {flow['spot_base']:.8g} BASE / future {flow['future_base']:.8g} BASE"
            )
        if x.get("estimated_net") is not None:
            out.append(
                f"Оценка NET выхода: {x['estimated_net']:+.4f} USD\nFunding: {'подтверждён' if x.get('funding_known') else 'ожидает сверки'}"
            )
        if x.get("cash_error"):
            out.append("Нужна сверка; повтор заявки запрещён.")
    inventory = (summary or {}).get("cash_inventory", [])
    if inventory:
        out.append("\n<b>Учёт оставшегося актива — не flat</b>")
        for x in inventory[:10]:
            out.append(
                f"{escape(x['venue'])}: {x['qty']:.8g} {escape(x['base'])} • стоимость {x['cost_usd']:.4f} USD удержана из cash NET"
            )
            out.append(
                "Баланс покрывает учёт."
                if x.get("balance_covered")
                else "Текущее покрытие балансом не подтверждено."
            )
    return "\n".join(out)


def menu(summary):
    rows = [[button("🧪 Проверить Spot/Futures без заявок", "sf_checks", "primary")]]
    for x in (summary or {}).get("trades", []):
        if x.get("strategy") != "spot_futures":
            continue
        tid = x["trade_id"]
        if len(("sf_recover:" + tid).encode()) > 64:
            continue
        if x["phase"] == "CASH_OPEN":
            rows.append(
                [
                    button(
                        "Закрыть " + x.get("spot_symbol", x["symbol"]),
                        "sf_close:" + tid,
                        "danger",
                    )
                ]
            )
        else:
            rows.append(
                [
                    button(
                        "Закрыть подтверждённый остаток", "sf_recover:" + tid, "danger"
                    )
                ]
            )
    rows.append([button("↻ Обновить", "sf_live"), button("‹ LIVE-контроль", "live")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
