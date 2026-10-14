from pathlib import Path
from aiogram.types import FSInputFile
async def send(bot,chat_id,path,caption="Audit export"):
 p=Path(path)
 if not p.exists():raise FileNotFoundError(path)
 await bot.send_document(chat_id,FSInputFile(p),caption=caption)
