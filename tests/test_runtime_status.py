from app.runtime_status import RuntimeStatus,render
def test_render():
 x=RuntimeStatus('PAPER',7,0,0,0,False,False,'LIVE_DISABLED')
 assert 'PAPER' in render(x) and 'LIVE switch: OFF' in render(x)
