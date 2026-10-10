from app.xlsx_export_model import sheets
def test_xlsx_model_has_required_sheets():assert set(sheets([],[],[],[]))=={"Observations","Trades","Replay","Ledger"}
