import asyncio
import copy
import json
import sqlite3
from types import SimpleNamespace
import pytest
from app.strategy_math import D, Invalid
from app.inventory_paper import Engine
from test_inventory_paper import engine, snapshot, book


class Source:
    def __init__(self,clock):
        self.clock=clock;self.a_size='100';self.b_size='100';self.b_ask='106.1';self.available=True;self.fee='.05'
    async def book(self,venue,asset):
        if venue=='a':return book('a',ts=self.clock[0],size=self.a_size)
        return book('b',ts=self.clock[0],ask=self.b_ask,bid='106',size=self.b_size)
    def rebalance(self,*args):
        rb=snapshot(self.clock[0])['rebalance'];rb['quote']['fee']=self.fee;return rb
    async def currencies(self,*args):pass
    async def network_check(self,*args):return self.available


async def opened(tmp_path,manual=True):
    e,s,clock=await engine(tmp_path)
    pos=await e.execute(s,s['books'][0],s['books'][1],s['books'][0],rebalance_mode='manual' if manual else 'auto')
    return e,pos,clock,Source(clock)


def run(fn):return asyncio.run(fn())


def test_manual_entry_holds_rebalancing_and_close_preserves_actual_loss(tmp_path):
    async def go():
        e,p,clock,source=await opened(tmp_path)
        assert p['status']=='AWAITING_REBALANCE' and not p['transfers'] and e.state['realized']=='0'
        await e.monitor(source);assert e.positions[str(p['id'])]['status']=='AWAITING_REBALANCE'
        view=await e.inspect_position(p['id'],source)
        assert D(view['rebalance_net'])>0 and D(view['close_net'])<0
        token=view['tokens']['close'];assert (await e.peek_action(token))['action']=='close'
        result=await e.request_action(token,source)
        assert result['status']=='CLOSED' and result['close_reason']=='MANUAL_SPOT_UNWIND'
        assert D(result['net'])<0 and e.state['losses']==1 and not e.positions
        with pytest.raises(Invalid,match='EXPIRED'):await e.request_action(token,source)
        assert e.state['closed']==1
        with sqlite3.connect(e.path) as db:
            stored=json.loads(db.execute('SELECT payload FROM inventory_paper_trades').fetchone()[0])
        assert any(x['side']=='manual_exit_sell' for x in stored['fills']) and stored['net']==result['net']
    run(go)


def test_manual_transfer_preserves_reservations_and_receipt_delay(tmp_path):
    async def go():
        e,p,clock,source=await opened(tmp_path)
        view=await e.inspect_position(p['id'],source);token=view['tokens']['transfer']
        result=await e.request_action(token,source)
        assert result['status']=='REBALANCE_PENDING' and len(result['transfers'])==2 and e.state['realized']=='0'
        clock[0]=1800;await e.monitor(source);assert e.state['closed']==0
        clock[0]=1901;await e.monitor(source)
        assert e.state['closed']==1 and D(e.state['realized'])>0
    run(go)


def test_manual_close_during_transfer_does_not_invent_refund_or_credit(tmp_path):
    async def go():
        e,p,clock,source=await opened(tmp_path,manual=False)
        view=await e.inspect_position(p['id'],source)
        assert set(view['tokens'])=={'finish'} and view['kind']=='transfers'
        balances=copy.deepcopy(e.state['balances']);due=[t['due'] for t in p['transfers']]
        result=await e.request_action(view['tokens']['finish'],source)
        assert result['manual_close_requested'] and result['status']=='REBALANCE_PENDING'
        assert e.state['balances']==balances and [t['due'] for t in result['transfers']]==due
        source.available=False;clock[0]=1901;await e.monitor(source)
        assert not e.state['closed'] and e.state['balances']==balances
        source.available=True;await e.monitor(source)
        assert e.state['closed']==1
        with sqlite3.connect(e.path) as db:
            stored=json.loads(db.execute('SELECT payload FROM inventory_paper_trades').fetchone()[0])
        assert stored['close_reason']=='MANUAL_REBALANCE_COMPLETE'
    run(go)


def test_partial_manual_exit_is_durable_and_resumes_without_double_fills(tmp_path):
    async def go():
        e,p,clock,source=await opened(tmp_path)
        view=await e.inspect_position(p['id'],source)
        source.a_size='.1';source.b_size='.05'
        result=await e.request_action(view['tokens']['close'],source)
        assert result['status']=='MANUAL_EXIT_PENDING' and D(result['exit_a_remaining'])==D('.15')
        assert D(result['exit_b_remaining'])==D('.20') and D(result['residual'])==D('.05')
        restored=Engine(e.path,clock=lambda:clock[0]);await restored.init()
        clock[0]+=2;source.a_size=source.b_size='100';await restored.monitor(source)
        assert restored.state['closed']==1 and not restored.positions
        balances=copy.deepcopy(restored.state['balances']);await restored.monitor(source)
        assert restored.state['balances']==balances and restored.state['closed']==1
    run(go)


def test_action_expiry_and_network_cost_change_never_mutate_balances(tmp_path):
    async def go():
        e,p,clock,source=await opened(tmp_path);view=await e.inspect_position(p['id'],source)
        balances=copy.deepcopy(e.state['balances']);clock[0]+=31
        with pytest.raises(Invalid,match='EXPIRED'):await e.request_action(view['tokens']['close'],source)
        view=await e.inspect_position(p['id'],source);source.fee='20'
        with pytest.raises(Invalid,match='COST_CHANGED'):await e.request_action(view['tokens']['transfer'],source)
        assert e.state['balances']==balances and not e.positions[str(p['id'])]['transfers']
    run(go)


def test_manual_exit_respects_cash_limit_and_does_not_borrow(tmp_path):
    async def go():
        e,p,clock,source=await opened(tmp_path);source.b_ask='10000'
        view=await e.inspect_position(p['id'],source);assert view['close_net'] is None
        result=await e.request_action(view['tokens']['close'],source)
        assert result['status']=='MANUAL_EXIT_PENDING' and D(result['exit_b_remaining'])>0
        assert all(D(v)>=0 for v in e.state['balances'].values()) and not result['transfers']
    run(go)


def test_closed_position_invalidates_all_other_action_cards(tmp_path):
    async def go():
        e,p,clock,source=await opened(tmp_path);view=await e.inspect_position(p['id'],source)
        await e.request_action(view['tokens']['close'],source)
        with pytest.raises(Invalid,match='STALE'):await e.request_action(view['tokens']['transfer'],source)
    run(go)


def test_candidate_alerts_are_durable_and_rate_limited(tmp_path):
    async def go():
        e,s,clock=await engine(tmp_path);plan=e.plan(s);events=[]
        async def send(kind,p):events.append(kind)
        e.on_event=send;await e.offer_candidate(plan);await e.offer_candidate(plan)
        assert events==['CANDIDATE'] and not e.positions and e.state['realized']=='0'
        clock[0]+=301;await e.offer_candidate(plan);assert events==['CANDIDATE','CANDIDATE']
    run(go)


def test_synthetic_demo_has_profit_and_loss_but_no_main_account():
    async def go():
        from app.inventory_simulation import run,render
        results=await run();assert len(results)==3
        assert D(results[0]['net'])>0 and D(results[1]['net'])<0
        assert all(x['closed']==1 for x in results)
        assert 'СИНТЕТИЧЕСКИЕ' in render(results)
    run(go)


def test_cards_show_prices_costs_and_negative_exit_estimate(tmp_path):
    async def go():
        from app.inventory_cards import entry_text,position_text,position_keyboard
        e,p,clock,source=await opened(tmp_path);text=entry_text(p['plan'])
        assert 'Купить' in text and 'Продать' in text and 'NET' in text and len(text)<4096
        view=await e.inspect_position(p['id'],source);text=position_text(view)
        assert 'NET, если полностью закрыть сейчас' in text and '-0.' in text and len(text)<4096
        assert len(position_keyboard(view).inline_keyboard)>=2
    run(go)


def test_changed_entry_risk_invalidates_candidate_before_spending(tmp_path):
    async def go():
        from app.inventory_cards import card_row
        e,s,clock=await engine(tmp_path);row=card_row(e.plan(s),e);balances=copy.deepcopy(e.state['balances'])
        await e.configure(max_notional='50')
        with pytest.raises(Invalid,match='ENTRY_CARD_STALE'):
            await e.execute(s,s['books'][0],s['books'][1],s['books'][0],expected_policy=row['inventory_policy'])
        assert not e.positions and e.state['balances']==balances
    run(go)


def test_telegram_position_confirm_action_reaches_actual_engine(tmp_path,monkeypatch):
    async def go():
        import app.main as main
        from app.trading_controls import Controls
        e,p,clock,source=await opened(tmp_path);edits=[];answers=[]
        main_bundle=SimpleNamespace(inventory_paper=e,inventory_source=source,runtime=SimpleNamespace(services={'spot_inventory':object()}))
        monkeypatch.setattr(main,'secondary',main_bundle)
        monkeypatch.setattr(main,'allowed',lambda uid:uid==1)
        monkeypatch.setattr(main,'scanner',SimpleNamespace(paused=False))
        monkeypatch.setattr(main,'strategy_runtime',SimpleNamespace(enabled={'spot_inventory':True}))
        monkeypatch.setattr(main,'trading_controls',Controls(tmp_path/'ctl'))
        async def edit(message,text,keyboard):edits.append((text,keyboard))
        monkeypatch.setattr(main,'safe_edit',edit)
        class Query:
            from_user=SimpleNamespace(id=1);message=object();data='inv:pos:'+str(p['id'])
            async def answer(self,*args,**kwargs):answers.append(args)
        q=Query();await main.inventory_action(q)
        callbacks=[b.callback_data for row in edits[-1][1].inline_keyboard for b in row]
        q.data=next(x for x in callbacks if x.startswith('inv:confirm:'))
        # Explicitly choose CLOSE rather than TRANSFER.
        close_token=next(token for token,t in e.action_tickets.items() if t['action']=='close')
        q.data='inv:confirm:'+close_token;await main.inventory_action(q)
        assert 'Подтверждение PAPER' in edits[-1][0]
        q.data='inv:act:'+close_token;await main.inventory_action(q)
        assert e.state['closed']==1 and 'Итоговый NET' in edits[-1][0]
        q.from_user=SimpleNamespace(id=2);await main.inventory_action(q)
        assert answers[-1]==('Нет доступа',) and e.state['closed']==1
    run(go)


def test_changed_operator_intent_during_quote_refresh_cannot_open(tmp_path):
    async def go():
        from app.inventory_public_source import Service
        e,s,clock=await engine(tmp_path)
        class Capture(Source):
            allowed_venue=staticmethod(lambda v:True)
            async def capture(self):return [snapshot(self.clock[0])]
            async def snapshot(self,asset,buy,sell):return snapshot(self.clock[0])
        source=Capture(clock);service=Service(source,e,allowed=lambda symbol=None:True)
        result=await service.enter_route('ETH','a','b',confirmation_guard=lambda:False)
        assert result is None and not e.positions and e.state['closed']==0
    run(go)
