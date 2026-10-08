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
    out.append("\n<i>LIVE/AUTO заблокированы до финальной проверки исполнения.</i>")
    return "\n".join(out)
