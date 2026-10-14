from app.spot_future_paper import open_from,mark
def test_paper_marks_basis_convergence():
 op={"base":"X","exchange":"a","direction":"LONG_SPOT_SHORT_FUTURE","notional":100,"base_qty":1,"prices":{"spot_buy":100,"spot_sell":99.9,"future_buy":110.1,"future_sell":110},"fee_pct":0,"safety_pct":0,"funding_pct":0};p=open_from(op);op["prices"]={"spot_buy":102.1,"spot_sell":102,"future_buy":106,"future_sell":105.9};assert mark(p,op)>0
