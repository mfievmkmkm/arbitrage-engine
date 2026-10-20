import asyncio
import sqlite3
import pytest
from app.cex_dex_paper import Engine
from app.paired_position_cards import Cards
from test_dex_outage_regressions import paired_source_with_rate


def test_manual_exit_survives_restart_and_outage_and_closes_both_legs_at_loss(tmp_path):
    async def run():
        source,route,public,provider,now,_=paired_source_with_rate()
        engine=Engine(tmp_path/'db',source,capital=250,virtual=True,namespace='paired',clock=source.clock)
        await engine.init();await engine.cycle(route)
        assert len(engine.positions)==1
        ident=next(iter(engine.positions))
        await engine.request_exit(ident)
        version=engine.version
        await engine.request_exit(ident)
        assert engine.version==version
        restored=Engine(engine.path,source,capital=250,virtual=True,namespace='paired',clock=source.clock)
        await restored.init()
        assert restored.positions[ident]['operator_exit_requested'] is not None
        exit_quote=source.exit
        async def unavailable(p):return dict(ok=False,reason='TEST_OUTAGE')
        source.exit=unavailable
        await restored.cycle(entry_enabled=False)
        assert ident in restored.positions and restored.used_capital>0
        source.exit=exit_quote
        closed=await restored.cycle(entry_enabled=False)
        assert len(closed)==1 and not restored.positions
        assert closed[0]['exit_reason']=='MANUAL_EXIT'
        assert closed[0]['net']<0  # User exit must not wait for positive NET.
        assert 'dex_pnl' in closed[0]['last_mark'] and 'cex_pnl' in closed[0]['last_mark']
        with pytest.raises(ValueError,match='ALREADY_CLOSED'):await restored.request_exit(ident)
    asyncio.run(run())


def test_cards_restart_cleanup_retry_preserves_financial_history(tmp_path):
    async def run():
        source,route,_,_,_,_=paired_source_with_rate()
        engine=Engine(tmp_path/'db',source,capital=250,virtual=True,namespace='paired',clock=source.clock)
        await engine.init();await engine.cycle(route)
        sent=[];deleted=[]
        async def deliver(text,keyboard,message_id):
            sent.append((text,message_id));return 91
        async def remove(message_id):deleted.append(message_id)
        cards=Cards(engine)
        await cards.sync(deliver,remove);await cards.sync(deliver,remove)
        assert len(sent)==1 and 'SHORT' in sent[0][0]
        ident=next(iter(engine.positions));await engine.request_exit(ident)
        await cards.sync(deliver,remove)
        assert sent[-1][1]==91
        await engine.cycle(entry_enabled=False)
        async def failure(message_id):raise TimeoutError()
        await cards.sync(deliver,failure)
        with sqlite3.connect(engine.path) as db:
            assert db.execute('SELECT COUNT(*) FROM paired_telegram_cards').fetchone()[0]==1
        await Cards(engine).sync(deliver,remove)
        await Cards(engine).sync(deliver,remove)
        assert deleted==[91]
        with sqlite3.connect(engine.path) as db:
            assert db.execute('SELECT COUNT(*) FROM paired_telegram_cards').fetchone()[0]==0
            assert db.execute(engine.sql('SELECT status FROM cex_dex_paper')).fetchone()[0]=='CLOSED'
    asyncio.run(run())


def test_manual_candidate_does_not_open_and_disabled_cycle_keeps_monitoring(tmp_path):
    from app.dex_paired_experiment import PairedCycle
    async def run():
        source,route,_,_,_,_=paired_source_with_rate()
        engine=Engine(tmp_path/'db',source,capital=250,virtual=True,namespace='paired',clock=source.clock)
        await engine.init()
        engine.allow_open=lambda p:False
        service=PairedCycle(engine,{})
        alerts=[]
        async def alert(candidate):alerts.append(candidate)
        service.notify_candidate=alert
        await service.cycle()
        assert len(alerts)==1 and not engine.positions
        assert alerts[0]['position']['virtual_mode']=='paired'
        engine.allow_open=lambda p:True
        await service.cycle()
        assert len(engine.positions)==1 and len(alerts)==1
        ident=next(iter(engine.positions));await engine.request_exit(ident)
        service.entry_enabled=False
        await service.cycle()
        assert not engine.positions and len(alerts)==1
    asyncio.run(run())
