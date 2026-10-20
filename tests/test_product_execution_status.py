import asyncio
import json
import sqlite3
from types import SimpleNamespace as NS

from app.product_execution_status import inspect, render


def test_missing_pipeline_is_not_ready_or_live():
    report=asyncio.run(inspect(NS()))
    assert not report['A']['pipeline_connected']
    assert not report['B']['pipeline_connected']
    assert not report['live_allowed']
    assert not report['wallet_address_grants_signing']
    assert not report['market_profitability_verified']
    assert 'виртуальное' in render(report)


def test_real_runtime_shape_checks_wiring_and_persists_evidence(tmp_path):
    from test_dex_transfer_paper import setup
    from app.dex_transfer_router import Router
    from test_final_spec_acceptance import staged_pair
    from app.decision_diagnostics import Service
    async def go():
        feed,handler,_,_,_=await setup(tmp_path)
        router=Router(feed,handler.paired)
        (tmp_path / "paired").mkdir()
        engine,*_=await staged_pair(tmp_path / "paired")
        engine.entry_executor=NS(source=engine.source)
        paired_service=NS(paper=engine,scanner=object())
        runtime=NS(services={'dex_paired':paired_service,'dex_transfer':router},
                   runtime=NS(enabled={'dex_paired':True,'product_dex_paired':True}))
        bundle=NS(runtime=runtime,dex_paired=engine,market_signals=feed)
        report=await inspect(bundle)
        assert report['A']['pipeline_connected'] and report['A']['entries_enabled']
        assert report['B']['pipeline_connected']
        assert report['B']['supports_both_transfer_directions']
        assert not report['current_product_live_execution_connected']
        diagnostics=Service(feed.journal,lambda:inspect(bundle))
        await diagnostics.cycle()
        with sqlite3.connect(feed.journal.path) as db:
            saved=json.loads(db.execute("SELECT payload FROM dex_scanner_audit WHERE scope='execution'").fetchone()[0])
        assert saved['A']['pipeline_connected'] and not saved['live_allowed']
        engine.entry_executor=NS(source=object())
        feed.paper.dex_transfer=object()
        broken=await inspect(bundle)
        assert not broken['A']['pipeline_connected']
        assert not broken['B']['pipeline_connected']
    asyncio.run(go())
