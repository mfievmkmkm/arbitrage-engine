from app.startup_report import render
class S:mode='PAPER';reason='NO_PRIVATE_VENUES';safe=True
def test_report():
 assert 'Private API' in render(S(),{},[])
