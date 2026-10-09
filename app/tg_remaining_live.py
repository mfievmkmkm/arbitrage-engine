from html import escape
from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button


def render(strategy, summary, coordinator, preview=None):
    label = "Spot/Spot" if strategy == "spot_spot" else "Funding"
    out = [f"📊 <b>{label} LIVE</b>"]
    out.append(
        "Запас актива на обеих биржах; переводы и займы не используются."
        if strategy == "spot_spot"
        else "Прогноз funding — для отбора. В NET входят только подтверждённые начисления; отрицательный NET входа запрещён."
    )
    if coordinator:
        out.append(
            "Последняя проверка: <code>"
            + escape(str(coordinator.latest.get("status", "—")))
            + "</code>"
        )
    if preview:
        out.append(
            "Проверка без заявок: <code>"
            + escape(str(preview.get("status", "—")))
            + "</code>"
        )
        if preview.get("reason"):
            out.append(escape(str(preview["reason"])))
    rows = [
        x for x in (summary or {}).get("trades", []) if x.get("strategy") == strategy
    ]
    if not rows:
        out.append("\nАктивных циклов нет.")
    for row in rows:
        out.append(
            f"\n<b>{escape(row['symbol'])}</b>\n<code>{escape(row['trade_id'])}</code>\nЭтап: {escape(row['phase'])} • private: {'сверено' if row.get('private_verified') else 'не подтверждено'}"
        )
        if row.get("estimated_net") is not None:
            out.append(f"NET выхода: {row['estimated_net']:+.4f} USD")
        if strategy == "spot_spot" and row.get("cashflow"):
            out.append(
                "Изменение запаса: "
                + " / ".join(
                    f"{escape(v)} {q:+.8g} BASE"
                    for v, q in row["cashflow"]["base"].items()
                )
            )
    return "\n".join(out)


def menu(strategy, summary):
    stem = "ss" if strategy == "spot_spot" else "funding"
    rows = [[button("🧪 Проверка без заявок", stem + "_checks", "primary")]]
    if strategy == "spot_spot":
        for x in (summary or {}).get("trades", []):
            if x.get("strategy") != strategy or not x.get("private_verified"):
                continue
            action = "close" if x["phase"] == "SS_OPEN" else "recover"
            if len(x["trade_id"]) <= 23:
                rows.append(
                    [
                        button(
                            (
                                "Закрыть цикл"
                                if action == "close"
                                else "Восстановить распределение"
                            ),
                            f"ss_{action}:" + x["trade_id"],
                            "danger",
                        )
                    ]
                )
    if strategy == "funding_arb":
        for x in (summary or {}).get("trades", []):
            if (
                x.get("strategy") == strategy
                and x.get("private_verified")
                and x["phase"] in ("OPEN", "HEDGED_PRIVATE_VERIFIED")
                and len(x["trade_id"]) <= 32
            ):
                rows.append(
                    [
                        button(
                            "Закрыть funding-позицию",
                            "fund_close:" + x["trade_id"],
                            "danger",
                        )
                    ]
                )
    rows.append([button("↻ Обновить", stem + "_live"), button("‹ LIVE", "live")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
