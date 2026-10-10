from dataclasses import dataclass
@dataclass(frozen=True)
class Recommendation:
 needed:bool;text:str

def build(need):
 if not need.needed:return Recommendation(False,"Балансировка не нужна")
 return Recommendation(True,f"Переместить {need.amount:.4f} USDT: {need.from_venue} -> {need.to_venue}. Выполнить вручную; withdrawal API выключен.")
