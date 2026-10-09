"""Shared Telegram visual hierarchy; style uses native Bot API button fills."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

STRATEGY_LABELS = {
    "futures_futures": "Фьючерсы ↔ Фьючерсы",
    "spot_futures": "Спот ↔ Фьючерсы",
    "spot_spot": "Спот ↔ Спот",
    "funding_arb": "Ставки funding",
    "cex_dex": "Биржи ↔ DEX",
}


def button(text, callback, style=None):
    return InlineKeyboardButton(
        text=text, callback_data=callback, **({"style": style} if style else {})
    )


def main_menu(paused=False):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button("⚡ Рынок", "top", "primary"),
                button("📈 Позиции", "paper", "primary"),
            ],
            [button("🧭 Стратегии", "strategies"), button("🏦 Площадки", "exchanges")],
            [
                button("📊 Аналитика", "strategy_stats"),
                button("🧪 Проверка истории", "replay"),
            ],
            [button("📔 Дневник", "diary"), button("💼 Капитал", "capital")],
            [
                button("📤 Выгрузить историю", "export"),
                button("🚦 Проверка запуска", "startup"),
            ],
            [button("🛡 Риски", "risk"), button("⚙️ Система", "status")],
            [button("⛓ DEX", "dex"), button("🔐 LIVE-контроль", "live")],
            [
                button(
                    "▶ Возобновить сканер" if paused else "⏸ Приостановить сканер",
                    "resume" if paused else "pause",
                    "success" if paused else None,
                )
            ],
            [button("🛑 Аварийный STOP", "live_stop", "danger")],
        ]
    )


def back_menu(screen=None, parent="home"):
    row = []
    if screen:
        row.append(button("↻ Обновить", screen, "primary"))
    row.append(button("‹ Главное меню" if parent == "home" else "‹ Назад", parent))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def live_menu(stopped, kill_active=False):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("🧪 Проверка аккаунтов и NET", "live_checks", "primary")],
            [button("📊 Spot/Futures LIVE", "sf_live", "primary")],
            *(
                [[button("Снять блокировку после сверки", "live_clear", "success")]]
                if kill_active
                else []
            ),
            [
                button("📈 LIVE-позиции", "live_positions"),
                button("🛡 Инциденты", "incidents"),
            ],
            [
                button(
                    "▶ Снять STOP" if stopped else "🛑 STOP",
                    "live_resume" if stopped else "live_stop",
                    "success" if stopped else "danger",
                )
            ],
            [button("↻ Обновить", "live"), button("‹ Главное меню", "home")],
        ]
    )


def replay_menu(screen="replay"):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button(
                    "Фьючерсы ↔ Фьючерсы",
                    "replay",
                    "primary" if screen == "replay" else None,
                )
            ],
            [
                button(
                    "Спот ↔ Фьючерсы",
                    "sf_replay",
                    "primary" if screen == "sf_replay" else None,
                )
            ],
            [
                button(
                    "Спот ↔ Спот",
                    "ss_replay",
                    "primary" if screen == "ss_replay" else None,
                )
            ],
            [
                button(
                    "Funding · история ставок",
                    "fund_replay",
                    "primary" if screen == "fund_replay" else None,
                )
            ],
            [
                button(
                    "⏱ Исполнение и задержки",
                    "execution_replay",
                    "primary" if screen == "execution_replay" else None,
                )
            ],
            [button("↻ Обновить", screen), button("‹ Главное меню", "home")],
        ]
    )


def strategy_menu(enabled):
    rows = [
        [
            button(
                ("● " if on else "○ ") + STRATEGY_LABELS.get(name, name),
                "strategy:" + name,
                "success" if on else None,
            )
        ]
        for name, on in enabled.items()
    ]
    rows.append([button("‹ Главное меню", "home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
