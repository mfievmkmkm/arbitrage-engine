async def edit(message,text,markup=None):
 try:await message.edit_text(text,reply_markup=markup,parse_mode="HTML")
 except Exception as e:
  if "message is not modified" not in str(e).lower():raise
