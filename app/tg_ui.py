from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚡ Рынок", callback_data="top"),
                InlineKeyboardButton(text="📈 Позиции", callback_data="paper"),
            ],
            [
                InlineKeyboardButton(text="🧭 Стратегии", callback_data="strategies"),
                InlineKeyboardButton(text="🏦 Площадки", callback_data="exchanges"),
            ],
            [
                InlineKeyboardButton(
                    text="📊 Аналитика", callback_data="strategy_stats"
                ),
                InlineKeyboardButton(text="🧪 Replay", callback_data="replay"),
            ],
            [
                InlineKeyboardButton(text="📔 Дневник", callback_data="diary"),
                InlineKeyboardButton(text="💼 Капитал", callback_data="capital"),
            ],
            [
                InlineKeyboardButton(text="📤 Export", callback_data="export"),
                InlineKeyboardButton(text="🚦 Startup", callback_data="startup"),
            ],
            [
                InlineKeyboardButton(text="🛡 Risk Center", callback_data="risk"),
                InlineKeyboardButton(text="⚙️ Система", callback_data="status"),
            ],
            [
                InlineKeyboardButton(text="⛓ DEX", callback_data="dex"),
                InlineKeyboardButton(text="⚡ LIVE", callback_data="live"),
            ],
            [
                InlineKeyboardButton(text="⏸ Сканер", callback_data="pause"),
                InlineKeyboardButton(text="▶ Возобновить", callback_data="resume"),
            ],
            [InlineKeyboardButton(text="🛑 EMERGENCY STOP", callback_data="live_stop")],
        ]
    )


def back_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="‹ Главное меню", callback_data="home")]
        ]
    )


def live_menu(stopped):
    rows = [
        [
            InlineKeyboardButton(
                text="📈 LIVE-позиции", callback_data="live_positions"
            ),
            InlineKeyboardButton(text="🛡 Инциденты", callback_data="incidents"),
        ],
        [
            InlineKeyboardButton(
                text="🛑 STOP" if not stopped else "🔐 Проверить и возобновить",
                callback_data="live_stop" if not stopped else "live_resume",
            )
        ],
        [InlineKeyboardButton(text="‹ Главное меню", callback_data="home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
