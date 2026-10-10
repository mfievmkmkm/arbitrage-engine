from app.executor_plan import build
def test_contract_sizes_are_equalized():
 p=build("X","a","b",.01,.001,.01,lambda x:round(x),lambda x:round(x))
 assert p.valid and p.long.contracts==10 and p.short.contracts==1
