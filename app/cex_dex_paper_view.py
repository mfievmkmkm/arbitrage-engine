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
    out.append(
        f"Резерв: {engine.used_capital:.2f} USD · позиций: {len(engine.positions)}"
    )
    if engine.state.get("last_decision"):
        out.append("Последний вход: " + escape(engine.state["last_decision"]))
    for p in engine.positions.values():
        out.append(
            f"\n<b>{escape(p['symbol'])}</b> · {escape(p['cex_venue'])}\n{'DEX BUY / CEX SHORT' if p['forward'] else 'DEX SELL / CEX LONG'}\nNET модели: {p['net']:+.4f} USD\n{escape(p['status'])} · {escape(p.get('funding_reason', 'история ещё не сверена'))}"
        )
        if p.get("data_reason", "OK") != "OK":
            out.append("Данные выхода: " + escape(p["data_reason"]))
    out.append(
        "\nПрогноз funding не зачисляется. Неполные данные удерживают позицию и резерв."
    )
    return "\n".join(out)
