import time
from html import escape


def render(engine, positions_only=False):
    out = ["\n\n🏦 <b>Спот ↔ Спот · Paper</b>"]
    if engine is None:
        return "\n".join(out + ["Модуль ещё не запущен."])
    if positions_only:
        out.append(f"Открытых позиций: <b>{len(engine.positions)}</b>")
        for p in engine.positions.values():
            out.append(
                f"\n<b>{escape(p['symbol'])}</b> · {escape(p['buy'])} ↔ {escape(p['sell'])}\nОбъём: {p['base_qty']:.8g} · NET {p['net']:+.4f} USD"
            )
            stamp = p.get("last_mark", {}).get("ts", 0)
            if not 0 <= time.time() - stamp <= 30:
                out.append("🟠 Оценка устарела")
            if p.get("exit_blocked"):
                out.append(
                    "🔴 Для восстановления актива не хватает USDT; выход не зачислен."
                )
        return "\n".join(out)
    state = engine.state
    out.append(
        f"Резерв бюджета: <b>{engine.used_capital:.2f} USD</b>\nЗакрытый NET: {state['realized']:+.4f} USD"
    )
    if not state["balances"]:
        out.append(
            "\nЗапасы не заданы. Возможности записываются, Paper-входы блокируются."
        )
    for venue, balances in state["balances"].items():
        out.append("\n<b>" + escape(venue.upper()) + "</b>")
        out.extend(escape(asset) + f": {qty:.8g}" for asset, qty in balances.items())
    out.append(
        "\n<i>Это виртуальные запасы по PAPER_SPOT_INVENTORY_JSON. Не баланс биржевого аккаунта. Продавать без актива, занимать и переводить монеты между биржами модуль не имитирует.</i>"
    )
    return "\n".join(out)
