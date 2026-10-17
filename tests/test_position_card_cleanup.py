import asyncio
import copy
from app.market_signal_store import Store
from app.transfer_signal_paper import Paper


def test_terminal_cards_retry_after_restart_without_losing_journal(tmp_path):
    async def run():
        store = Store(tmp_path/'db', lambda: 1000)
        await store.init()
        paper = Paper(store, lambda: 1000)
        await paper.init()
        state = copy.deepcopy(paper.state)
        state.update(last_position={'id': 'closed', 'status': 'CLOSED', 'net': '-1'},
                     realized='-1', closed=1,
                     position_cards={'closed': {'message_id': 42}, 'older': {'message_id': 40}})
        await paper.save(state, 'TEST_CLOSED', state['last_position'])
        async def deliver(*args): raise AssertionError('Do not recreate closed cards')
        calls = []
        async def failed_delete(message_id):
            calls.append(message_id)
            raise TimeoutError()
        await paper.sync_cards(deliver, failed_delete)
        assert len(paper.state['position_cards']) == 2
        restored = Paper(store, lambda: 1000)
        await restored.init()
        async def remove(message_id): calls.append(message_id)
        await restored.sync_cards(deliver, remove)
        await restored.sync_cards(deliver, remove)
        assert restored.state['position_cards'] == {}
        assert calls == [42, 40, 42, 40]
        assert restored.state['realized'] == '-1'
        assert restored.state['last_position']['net'] == '-1'
        assert restored.state['closed'] == 1
    asyncio.run(run())


def test_card_notifications_do_not_duplicate_lifecycle(tmp_path):
    async def run():
        store = Store(tmp_path/'db', lambda: 1000)
        await store.init()
        paper = Paper(store, lambda: 1000)
        await paper.init()
        state = copy.deepcopy(paper.state)
        state['notifications'] = {'open:1': 'opened', 'close:1': 'closed', 'incident:1': 'risk'}
        await paper.save(state, 'TEST', {})
        sent = []
        async def notify(text): sent.append(text)
        await paper.flush(notify, quiet_lifecycle=True)
        assert sent == ['risk']
        assert paper.state['notifications'] == {}
    asyncio.run(run())
