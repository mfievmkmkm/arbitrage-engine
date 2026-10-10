from aiogram.types import InlineKeyboardMarkup
from .tg_ui import button


def list_keyboard(names):
    rows = [
        [button(n.upper(), "venue:" + n) for n in names[i : i + 2]]
        for i in range(0, len(names), 2)
    ]
    rows.append([button("↻ Обновить", "exchanges"), button("‹ Главное меню", "home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def control(name, m):
    def toggle(k, label):
        return button(
            label + (" · ВКЛ" if m.get(k) else " · ВЫКЛ"),
            f"vt:{name}:{k}",
            "success" if m.get(k) else None,
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [toggle("scan", "Сканер"), toggle("paper", "Paper")],
            [button("🔐 Реальная торговля · проверка допуска", f"vt:{name}:real")],
            [button("‹ Площадки", "exchanges")],
        ]
    )
