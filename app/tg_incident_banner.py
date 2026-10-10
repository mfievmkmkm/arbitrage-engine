from html import escape


def unknown_label(value):
    if isinstance(value, bool):
        return "есть · требуется сверка" if value else "нет"
    if isinstance(value, (list, tuple, set, dict)):
        return str(len(value))
    return escape(str(value))


def render(supervisor, stop):
    items = []
    if stop.stopped:
        items.append("🛑 <b>STOP активен</b>")
    if supervisor.unknown_orders:
        items.append(
            "🔴 <b>Неизвестные заявки: "
            + unknown_label(supervisor.unknown_orders)
            + "</b>"
        )
    if not supervisor.restart_clean:
        items.append("🔴 Требуется сверка после перезапуска")
    if not supervisor.private_verified:
        items.append("🟠 Позиции бирж не подтверждены")
    return "\n".join(items)
