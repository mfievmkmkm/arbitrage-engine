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


def render(
    supervisor,
    trades,
    realized_net,
    stop,
    exit_configured=False,
    entry_configured=False,
    entry_status=None,
):
    r = supervisor.readiness()
    rows = [
        "🔐 <b>LIVE-контроль</b>",
        (
            "Новые реальные входы: <b>настроены</b>"
            if entry_configured
            else (
                "Новые реальные входы: <b>заблокированы</b>"
                if exit_configured
                else "Реальная торговля: <b>заблокирована</b>"
            )
        ),
        f"Сохранённых позиций: {len(trades)}",
        f"Подтверждённый NET: <b>{realized_net:+.4f} USD</b>",
    ]
    if entry_configured and entry_status:
        rows.append(
            "Последняя проверка входа: " + escape(str(entry_status.get("status", "—")))
        )
    if stop.stopped:
        rows.append("STOP: " + escape(stop.reason))
    if exit_configured:
        rows.append(
            "Автовыход: <b>настроен</b>; исполнение требует снятого STOP и свежей сверки."
        )
    if r.reasons:
        rows.append(
            ("\nПроверки наблюдателя:\n" if entry_configured else "\nБлокировки:\n")
            + "\n".join("• " + escape(REASONS.get(x, x)) for x in r.reasons)
        )
    rows.append(
        "\nДопуск IOC-входа проверяется перед каждой отправкой; STOP и release/venue evidence обязательны."
        if entry_configured
        else "\nНовые реальные входы: <b>запрещены</b>. Проверка данных не включает торговлю."
    )
    return "\n".join(rows)
