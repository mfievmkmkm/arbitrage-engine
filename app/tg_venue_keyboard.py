from aiogram.types import InlineKeyboardButton,InlineKeyboardMarkup
def list_keyboard(names):
 rows=[]
 for i in range(0,len(names),2):rows.append([InlineKeyboardButton(text=n.upper(),callback_data="venue:"+n) for n in names[i:i+2]])
 rows.append([InlineKeyboardButton(text="‹ Меню",callback_data="home")]);return InlineKeyboardMarkup(inline_keyboard=rows)
def control(name,m):
 def s(k):return "ON" if m.get(k) else "OFF"
 return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"SCAN · {s('scan')}",callback_data=f"vt:{name}:scan"),InlineKeyboardButton(text=f"PAPER · {s('paper')}",callback_data=f"vt:{name}:paper")],[InlineKeyboardButton(text=f"REAL · {s('real')}",callback_data=f"vt:{name}:real")],[InlineKeyboardButton(text="‹ Площадки",callback_data="exchanges")]])
