from .tg_ui import STRATEGY_LABELS
from html import escape


def render(rows):
    lines = [
        "📊 <b>Аналитика наблюдений</b>",
        "<i>Статистика найденных edge, не доходность торговли.</i>",
    ]
    if not rows:
        lines.append("\nПока нет сохранённых наблюдений.")
    for x in rows:
        lines.append(
            f"\n<b>{escape(STRATEGY_LABELS.get(x['strategy'],x['strategy']))}</b>\nНаблюдений: {x['observations']}\nСредний edge: {x['avg_edge'] or 0:+.3f}% · лучший: {x['best_edge'] or 0:+.3f}%"
        )
    return "\n".join(lines)
