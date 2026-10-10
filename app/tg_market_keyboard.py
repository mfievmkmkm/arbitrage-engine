import hashlib, json
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from .tg_ui import STRATEGY_LABELS, button


def selection_key(name, row):
    fields = [name] + [
        row.get(k)
        for k in ("symbol", "base", "buy", "sell", "exchange", "direction", "ts")
    ]
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]


def build(rows):
    buttons = []
    for i, (edge, name, x) in enumerate(rows[:8]):
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{i+1:02d} · {STRATEGY_LABELS.get(name,name)} · {edge:+.2f}%",
                    callback_data="opp:" + selection_key(name, x),
                )
            ]
        )
    buttons.append(
        [
            button("↻ Обновить", "top", "primary"),
            InlineKeyboardButton(text="‹ Меню", callback_data="home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
