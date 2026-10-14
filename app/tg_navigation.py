from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup
def section(back="home",extra=()):
 rows=[[InlineKeyboardButton(text=t,callback_data=c)] for t,c in extra];rows.append([InlineKeyboardButton(text="‹ Назад",callback_data=back)]);return InlineKeyboardMarkup(inline_keyboard=rows)
