from html import escape

REASONS = {
    "CI_RED": "нет подтверждённого CI для допуска",
    "PRIVATE_UNVERIFIED": "позиции не подтверждены",
    "RESTART_UNSAFE": "восстановление не сверено",
    "FEE_UNVERIFIED": "комиссии не подтверждены",
    "FUNDING_UNKNOWN": "funding не подтверждён",
    "BOOK_STALE": "стакан устарел",
    "KILL_SWITCH": "аварийная блокировка",
    "UNKNOWN_ORDERS": "неизвестный результат заявок",
    "LIVE_E2E_MISSING": "нет сквозной проверки реального исполнения",
}


def render(supervisor, trades, realized_net, stop):
    r = supervisor.readiness()
    rows = [
        "🔐 <b>LIVE-контроль</b>",
        "Реальная торговля: <b>заблокирована</b>",
        f"Сохранённых позиций: {len(trades)}",
        f"Подтверждённый NET: <b>{realized_net:+.4f} USD</b>",
    ]
    if stop.stopped:
        rows.append("STOP: " + escape(stop.reason))
    if r.reasons:
        rows.append(
            "\nБлокировки:\n"
            + "\n".join("• " + escape(REASONS.get(x, x)) for x in r.reasons)
        )
    rows.append(
        "\nНовые реальные входы: <b>запрещены</b>. Проверка данных не включает торговлю."
    )
    return "\n".join(rows)
