from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup
def build(rows):
 buttons=[]
 for i,(edge,name,x) in enumerate(rows[:8]):buttons.append([InlineKeyboardButton(text=f"{i+1:02d} · {name} · {edge:+.2f}%",callback_data=f"opp:{i}")])
 buttons.append([InlineKeyboardButton(text="↻ Обновить",callback_data="top"),InlineKeyboardButton(text="‹ Меню",callback_data="home")]);return InlineKeyboardMarkup(inline_keyboard=buttons)
