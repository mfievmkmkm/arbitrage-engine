"""Inspect actual PAPER wiring, not profitability or LIVE acceptance."""
from html import escape


async def inspect(bundle,controls=None,paused=False):
    services=getattr(getattr(bundle,'runtime',None),'services',{})
    engine=getattr(bundle,'dex_paired',None)
    a_service=services.get('dex_paired')
    feed=getattr(bundle,'market_signals',None)
    b_service=services.get('dex_transfer')
    profile=controls.profile('paper') if controls else {}
    b=await feed.store.control() if feed else {}
    toggles=getattr(getattr(getattr(bundle,'runtime',None),'runtime',None),'enabled',{})
    executor=getattr(engine,'entry_executor',None)
    a_wired=bool(engine and executor and getattr(executor,'source',None) is engine.source
                 and a_service and getattr(a_service,'paper',None) is engine
                 and getattr(a_service,'scanner',None))
    b_wired=bool(feed and b_service and getattr(b_service,'paper',None) is feed.paper
                 and getattr(feed.paper,'dex_transfer',None) is b_service)
    account_venues=[]
    if feed:
        for venue in sorted(feed.trade_venues):
            reader=feed.accounts.client(venue)
            has=getattr(reader,'has',{})
            if has.get('fetchCurrencies') is True and has.get('fetchTradingFee') is True:
                account_venues.append(venue)
    return dict(evidence_mode='READ_ONLY_EXECUTION_WIRING',paper_execution_is_modelled=True,
        market_profitability_verified=False,live_allowed=False,
        current_product_live_execution_connected=False,wallet_address_grants_signing=False,
        paused=bool(paused),
        A=dict(pipeline_connected=a_wired,mode=profile.get('mode','unknown'),
               entries_enabled=bool(toggles.get('dex_paired') and toggles.get('product_dex_paired')),
               size_usd=profile.get('notional_usd'),positions=len(getattr(engine,'positions',{})),
               supported_direction='DEX_SPOT_BUY_AND_CEX_FUTURES_SHORT',
               reverse_requires_inventory_or_borrow=True),
        B=dict(pipeline_connected=b_wired,mode=b.get('mode','unknown'),
               entries_enabled=bool(feed and feed.entry_enabled and b_service and b_service.entry_enabled and await feed.store.enabled()),
               size_usd=b.get('budget'),account_read_venues=account_venues,
               dex_chains=[1,56],supports_both_transfer_directions=True,
               has_position=bool(feed and feed.paper.state.get('position'))),
        live_requirements=['SEPARATE_EXPLICIT_AUTHORIZATION','PRODUCT_LIVE_EXECUTOR_ACCEPTANCE',
            'ISOLATED_WALLET_SIGNING','RPC_AND_TRANSACTION_SIMULATION','GAS_AND_TOKEN_BALANCES',
            'CEX_ORDER_AND_WITHDRAW_ACCESS','EXACT_NETWORK_AND_DEPOSIT_DESTINATION',
            'RECEIPT_RECONCILIATION_AND_PARTIAL_LEG_RECOVERY'])


def render(report):
    modes={'auto':'Авто','manual':'Вручную','observe':'Наблюдение','unknown':'не настроен'}
    a,b=report['A'],report['B']
    lines=['⚙️ <b>Исполнение · PAPER</b>']
    for name,row in (('A · сжатие',a),('B · перевод',b)):
        status='подключён' if row['pipeline_connected'] else 'не подключён'
        lines.append(f'{name}: цикл {status}\nРежим: {modes.get(row["mode"],"неизвестен")} · входы '+('включены' if row['entries_enabled'] and not report['paused'] else 'выключены'))
    lines.append('A: DEX BUY + Futures SHORT.\nB: CEX Spot ↔ DEX, оба направления.')
    lines.append('B · API чтения: '+escape(', '.join(b['account_read_venues']) or 'не подключены'))
    lines.append('<b>DEX-исполнение сейчас виртуальное.</b>\nРеальные swaps и переводы в этих циклах не подключены. Адрес кошелька не даёт права подписи.')
    lines.append('Для LIVE нужны отдельный допуск, изолированная подпись, RPC, gas, доступы биржи и проверка транзакций.\nРабота цикла не подтверждает наличие прибыльной возможности.')
    return '\n\n'.join(lines)
