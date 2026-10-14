from app.trade_summary import summarize
def test_summary():
 x=summarize([{"kind":"FILL","fee":.1,"venue":"a"},{"kind":"STATE","reason":"HEDGED"}])
 assert x["fees"]==.1 and x["states"]==["HEDGED"]
