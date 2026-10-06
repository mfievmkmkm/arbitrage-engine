from app.funding_calendar import Settlement,upcoming
def test_funding_calendar_filters_horizon():
 r=[Settlement("a","X",.1,110,8),Settlement("b","X",.1,999,8)];assert len(upcoming(r,100,20))==1
