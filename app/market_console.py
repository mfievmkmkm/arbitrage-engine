import math
from html import escape
from .tg_ui import STRATEGY_LABELS


def merged(runtime, limit=12):
    out = []
    for name, rows in runtime.latest.items():
        for x in rows:
            y = dict(x) if isinstance(x, dict) else dict(x.__dict__)
            if not any(k in y for k in ("hypothetical_edge", "net", "carry_pct")):
                continue
            try:
                edge = float(
                    y.get("hypothetical_edge", y.get("net", y.get("carry_pct", 0)))
                )
            except (TypeError, ValueError):
                continue
            if math.isfinite(edge):
                out.append((edge, name, y))
    return sorted(out, key=lambda x: x[0], reverse=True)[:limit]


def render(runtime):
    rows = merged(runtime)
    out = [
        "⚡ <b>Рынок</b>",
        "<i>Оценки по текущим данным. Выбери возможность для подробностей.</i>",
    ]
    if not rows:
        return "\n\n".join(out + ["Сейчас подходящих возможностей нет."])
    for i, (edge, name, x) in enumerate(rows, 1):
        symbol = escape(x.get("symbol") or x.get("base", "—"))
        venue = escape(x.get("exchange") or x.get("buy") or x.get("long_venue", ""))
        label = "Прогноз carry" if name == "funding_arb" else "Оценка NET"
        out.append(
            f"<b>{i:02d} · {symbol}</b>\n{STRATEGY_LABELS.get(name,escape(name))} · {venue}\n{label}: <b>{edge:+.3f}%</b>"
        )
    return "\n\n".join(out)
