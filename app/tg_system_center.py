from .tg_ui import STRATEGY_LABELS
from .tg_format import age


def render(scanner, runtime, secondary=None):
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
    clients = list(scanner.clients.values()) + list(
        getattr(secondary, "clients", {}).values()
    )
    clients = list({id(c): c for c in clients}.values())
    statuses = [
        c.book_status() for c in clients if callable(getattr(c, "book_status", None))
    ]
    if statuses:
        streams = sum(s["streams"] for s in statuses)
        out.extend(
            [
                "\n<b>Публичные стаканы</b>",
                (
                    f"WebSocket: {streams} клиентов; REST — резервный источник"
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
    recorder = getattr(secondary, "book_recorder", None)
    if recorder is not None:
        stats = recorder.status()
        out.append(
            f"История Spot/Futures и Spot/Spot: {stats['recorded']} снимков • объединено: {stats['coalesced']} • потеряно: {stats['dropped']}"
        )
        out.append(f"Ошибки вторичной записи: {stats['failures']}")
    funding_source = getattr(getattr(secondary, "funding_paper", None), "source", None)
    funding_history = getattr(funding_source, "history_store", None)
    if funding_history is not None:
        out.append(
            f"История funding: {funding_history.recorded} проверенных окон • ошибок записи/проверки: {funding_history.failures}"
        )
        out.append(
            f"Стаканы Funding: {getattr(funding_source, 'book_recorded', 0)} • ошибок записи: {getattr(funding_source, 'book_record_failures', 0)}"
        )
    out.append("\n<i>Настройки и допуск реального исполнения — в LIVE-контроле.</i>")
    return "\n".join(out)
