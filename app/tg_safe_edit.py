from aiogram.exceptions import TelegramBadRequest


async def edit(message, text, markup=None):
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as error:
        reason = str(error).lower()
        if "message is not modified" in reason:
            return
        if "message to edit not found" in reason or "message can't be edited" in reason:
            await message.answer(text, reply_markup=markup, parse_mode="HTML")
            return
        raise
