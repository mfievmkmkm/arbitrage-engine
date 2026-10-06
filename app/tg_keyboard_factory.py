from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup
def venue_list(names):
 rows=[]
 for i in range(0,len(names),2):rows.append([InlineKeyboardButton(text=x.upper(),callback_data="venue:"+x) for x in names[i:i+2]])
 rows.append([InlineKeyboardButton(text="‹ Главное меню",callback_data="home")]);return InlineKeyboardMarkup(inline_keyboard=rows)
def venue(name):return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="SCAN",callback_data=f"vm:{name}:scan"),InlineKeyboardButton(text="PAPER",callback_data=f"vm:{name}:paper"),InlineKeyboardButton(text="REAL",callback_data=f"vm:{name}:real")],[InlineKeyboardButton(text="‹ Площадки",callback_data="exchanges")]])
def strategy_list(names):return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=n,callback_data="strategy:"+k)] for k,n in names]+[[InlineKeyboardButton(text="‹ Главное меню",callback_data="home")]])
