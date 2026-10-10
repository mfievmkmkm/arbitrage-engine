from .tg_ui import STRATEGY_LABELS
from .tg_format import age


def render(scanner, runtime):
    out = [
        "⚙️ <b>Система</b>",
        f"\nСканер: {'⏸ На паузе' if scanner.paused else '🟢 Работает'}",
        f"Площадки: <b>{len(scanner.clients)}/{len(scanner.ids)}</b>",
        f"Последний цикл: {age(scanner.last_scan)}",
        "\n<b>Наблюдения по стратегиям</b>",
    ]
    out.extend(
        f"{STRATEGY_LABELS.get(k,k)}: <b>{v}</b>" for k, v in runtime.counts().items()
    )
    statuses = [
        c.book_status()
        for c in scanner.clients.values()
        if callable(getattr(c, "book_status", None))
    ]
    if statuses:
        streams = sum(s["streams"] for s in statuses)
        out.extend(
            [
                "\n<b>Публичные стаканы</b>",
                (
                    f"WebSocket: {streams} площадок; REST — резервный источник"
                    if streams
                    else "Источник: проверяемые REST-стаканы"
                ),
                f"Подписок: {sum(s['subscribed'] for s in statuses)} • свежих: {sum(s['fresh'] for s in statuses)}",
                f"Чтения WS / REST: {sum(s.get('ws_reads', 0) for s in statuses)} / {sum(s.get('rest_reads', 0) for s in statuses)}",
                f"Потоков с ошибкой: {sum(s['errors'] for s in statuses)}",
                f"Смен подписок: {sum(s.get('rotated', 0) for s in statuses)} • удержано при неопределённом снятии: {sum(s.get('retained', 0) for s in statuses)}",
            ]
        )
    recorder = getattr(scanner, "book_recorder", None)
    if recorder is not None:
        stats = recorder.status()
        out.append(
            f"История WS: {stats['recorded']} снимков • объединено: {stats['coalesced']} • потеряно: {stats['dropped']}"
        )
        out.append(f"Ошибки записи: {stats['failures']}")
    out.append("\n<i>Настройки и допуск реального исполнения — в LIVE-контроле.</i>")
    return "\n".join(out)
