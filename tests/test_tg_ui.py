from app.tg_ui import main_menu,live_menu
def test_main_menu_has_emergency_stop():
 labels=[b.text for r in main_menu().inline_keyboard for b in r];assert "🛑 EMERGENCY STOP" in labels
def test_stopped_live_menu_offers_evidence_resume():
 labels=[b.text for r in live_menu(True).inline_keyboard for b in r];assert any("Проверить" in x for x in labels)
