import asyncio
from types import SimpleNamespace as NS
import pytest
from app.numeric_settings import parse


@pytest.mark.parametrize('text', ['NaN','inf','1e8','-2','1 000','12 USD','', '9'*33])
def test_invalid_operator_number(text):
    with pytest.raises(ValueError):parse(text)


def test_decimal_comma_and_integer():
    assert parse(' 7,35 ')==7.35
    assert parse('2.0',True)==2
    with pytest.raises(ValueError):parse('2,5',True)


def test_manual_input_persists_without_duplicating_messages_and_back_revokes(tmp_path,monkeypatch):
    async def go():
        import app.main as main
        from app.market_signal_store import Store
        from app.trading_controls import Controls
        store=Store(tmp_path/'db');await store.init()
        feed=NS(store=store,paper=NS(state={}))
        monkeypatch.setattr(main,'secondary',NS(market_signals=feed))
        monkeypatch.setattr(main,'allowed',lambda uid:uid==1)
        monkeypatch.setattr(main,'trading_controls',Controls(tmp_path/'ctl'))
        monkeypatch.setattr(main,'scanner',NS(paused=False))
        edits=[];deletes=[]
        async def edit(text,**kw):edits.append((text,kw))
        async def delete():deletes.append(True)
        msg=NS(from_user=NS(id=1),chat=NS(id=10),text='7,35',bot=NS(edit_message_text=edit),delete=delete)
        main.numeric_pending[1]=dict(kind='sig',key='min_spread',chat=10,message=50,expires=main.time.time()+120)
        await main.numeric_setting_message(msg)
        assert (await store.control())['min_spread']==7.35
        assert edits[-1][1]['message_id']==50 and deletes and 1 not in main.numeric_pending
        main.numeric_pending[1]=dict(kind='sig',key='min_spread',chat=10,message=50,expires=main.time.time()+120)
        msg.text='2'
        await main.numeric_setting_message(msg)
        assert (await store.control())['min_spread']==7.35 and 1 in main.numeric_pending
        called=[]
        async def handler(*a):called.append(True)
        await main.clear_numeric_prompt(handler,NS(from_user=NS(id=1)),{})
        assert called and 1 not in main.numeric_pending
    asyncio.run(go())


def test_expired_and_cross_chat_input_cannot_change_settings(tmp_path,monkeypatch):
    async def go():
        import app.main as main
        from app.market_signal_store import Store
        store=Store(tmp_path/'db');await store.init()
        monkeypatch.setattr(main,'secondary',NS(market_signals=NS(store=store)))
        monkeypatch.setattr(main,'allowed',lambda _:True)
        async def noop(*a,**kw):pass
        m=NS(from_user=NS(id=99),chat=NS(id=2),text='40',bot=NS(edit_message_text=noop),delete=noop)
        main.numeric_pending[99]=dict(kind='sig',key='budget',chat=1,message=5,expires=0)
        await main.numeric_setting_message(m)
        assert (await store.control())['budget']==25
        m.chat.id=1
        await main.numeric_setting_message(m)
        assert (await store.control())['budget']==25 and 99 not in main.numeric_pending
    asyncio.run(go())


def test_paired_input_is_scoped_to_environment_revision(tmp_path,monkeypatch):
    async def go():
        import app.main as main
        from app.trading_controls import Controls
        ctl=Controls(tmp_path/'ctl');monkeypatch.setattr(main,'trading_controls',ctl)
        monkeypatch.setattr(main,'allowed',lambda _:True)
        monkeypatch.setattr(main,'apply_operator_paper_limits',lambda:None)
        async def noop(*a,**kw):pass
        monkeypatch.setattr(main,'diary',NS(record_decisions=noop))
        m=NS(from_user=NS(id=55),chat=NS(id=3),text='37,5',bot=NS(edit_message_text=noop),delete=noop)
        def pending():
            main.numeric_pending[55]=dict(kind='ctl',key='notional_usd',chat=3,message=4,
                expires=main.time.time()+120,environment=ctl.environment,revision=ctl.state['revision'])
        pending();await main.numeric_setting_message(m)
        assert ctl.profile()['notional_usd']==37.5
        pending();ctl.update(environment='live')
        await main.numeric_setting_message(m)
        assert ctl.profile()['notional_usd']==5 and ctl.profile()['mode']=='observe'
        assert 55 not in main.numeric_pending
    asyncio.run(go())
