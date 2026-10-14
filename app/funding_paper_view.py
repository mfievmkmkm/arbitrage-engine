from html import escape


def render(engine):
    out = [
        "\n\n🕒 <b>Funding · Paper</b>",
        "<i>Исторические ставки × зафиксированный reference notional. Это модель, не выплата аккаунта.</i>",
    ]
    if engine is None:
        return "\n".join(out + ["Модуль ещё не подключён."])
    out.append(
        f"Резерв двух ног: {engine.used_capital:.2f} USD · позиций: {len(engine.positions)}"
    )
    for p in engine.positions.values():
        out.append(
            f"\n<b>{escape(p['symbol'])}</b>\n{escape(p['buy'])} LONG ↔ {escape(p['sell'])} SHORT\nNET модели: {p['net']:+.4f} USD\nИстория: {escape(p.get('funding_reason','ещё не сверена'))}"
        )
        if p["status"] == "EXIT_ACCOUNTING_PENDING":
            out.append(
                "Выход зафиксирован по модели; резерв удерживается до проверки истории funding."
            )
        elif p.get("data_reason") and p["data_reason"] != "OK":
            out.append("Данные выхода: " + escape(p["data_reason"]))
    out.append(
        "\n<i>Прогноз ставки не зачисляется. Недоступная история и пропущенные события блокируют окончательный NET.</i>"
    )
    return "\n".join(out)
