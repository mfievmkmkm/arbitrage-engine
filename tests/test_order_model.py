from app.order_model import Order,Fill,OrderState
def test_fill_lifecycle():
 o=Order("a","X","buy",2);o.add_fill(Fill(1,10));assert o.state==OrderState.PARTIAL
 o.add_fill(Fill(1,12));assert o.state==OrderState.FILLED and o.avg_price==11
