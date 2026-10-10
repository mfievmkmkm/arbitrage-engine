from html import escape
from .tg_incident_banner import unknown_label


def render(risk, supervisor, stop):
    s = risk.state
    return (
        "🛡 <b>Контроль риска</b>\n\n"
        f"Система: {'🔴 Остановлена' if s.halted else '🟢 Норма'}\nПричина: {escape(getattr(s,'reason','') or '—')}\n"
        f"STOP: {'🔴 Активен' if stop.stopped else 'снят'}\nДанные позиций: {'🟢 Подтверждены' if supervisor.private_verified else '🟠 Не подтверждены'}\n"
        f"Восстановление: {'🟢 Сверено' if supervisor.restart_clean else '🔴 Требуется сверка'}\nНеизвестные заявки: <b>{unknown_label(supervisor.unknown_orders)}</b>\n\n"
        f"NET Paper за день: <b>{s.paper_daily_pnl:+.4f} USD</b>\nОшибок подряд: <b>{getattr(s,'consecutive_errors',0)}</b>\n\n"
        "<i>Неподтверждённые позиции блокируют новые реальные входы.</i>"
    )
