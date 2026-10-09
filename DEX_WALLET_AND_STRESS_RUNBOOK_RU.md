# DEX wallet backend и stress исполнения

Дата: 9 октября 2026. Этот документ описывает реализованные компоненты, не production-допуск.

## Следующий реализованный блок: durable bridge session API

`app/dex_live_bridge.py` связывает реальный receipt с CEX native sizing, terminal/private hedge, закрытием derivative и обратным swap. Forward: exact-out покупка токена → short CEX → reduce-only buy → exact-in продажа всего actual asset. Reverse: exact-in продажа заранее принадлежащего токена → long CEX → reduce-only sell → exact-out восстановление точного raw amount. Каждый CEX/exit stage сохраняется до send; claimed stage без durable terminal intent остаётся HOLD. Recovery — отдельный явно вызванный API, максимум 3 раунда, свежая private/receipt сверка перед каждым. UNKNOWN не повторяется.

`app/dex_cex_backend.py` использует существующие SafeExecutor, IOC quote evidence, reduce-only recovery evidence, client IDs, durable private order reconciliation и точные contractSize units. Если точный токеновый объём не представим native quantity, вход блокируется — пыль не скрывается.

`app/dex_live_admission.py` сверяет expiring token/CEX registry и CEX account acceptance, one-way/flat isolated account, margin, equity/daily loss, fee, funding calendar, обратную firm simulation и свежие книги. `hedge_admission=admission.hedge` обязательно заново проверяет границы после wallet confirmation. Положительный full-convergence ceiling — модельный допуск, не реализованный NET и не обещание схождения.

`app/dex_live_costs.py` собирает mature private funding и оценивает фактически оплаченный ETH gas в USDT по свежей исполнимой ETH/USDT ask depth. Это replacement valuation, не фактический обмен ETH на USDT и не USD-курс. Допускается только проверенный mainnet USDT quote contract/6 decimals; funding/gas/quote evidence хранится с результатом.

`Session.finalize` требует нулевой asset delta, private CEX flat, неизменные обе durable journals, fresh dual-RPC current wallet state и покрытые costs. Result, единственный LIVE_NET ledger event и освобождение общего owner коммитятся атомарно. Конкурентный finalize не начисляет повторный итог. Новые receipts сохраняют native before/after balance: старый неполный proof не мигрируется в доверенный автоматически.

В secondary bootstrap добавлен `dex_live_observation`; общему LIVE monitor передан read-only observer. Main не создаёт signer и не вызывает enter/hedge/close/finalize этого Session API. Ещё требуется production write-bootstrap/scanner admission integration и paired executable dynamic NET exit monitor; затем реальная приёмка и длительная Paper/OOS история. Не включайте DEX LIVE по факту unit-тестов.

## Что подключено

`/dex_stress` моделирует последовательный DEX → CEX цикл по записанным firm quotes и публичным native стаканам. Сценарии: quote-bound baseline, задержка DEX 2/15 секунд плюс CEX 0.5/1 секунда, известный entry/exit revert и UNKNOWN receipt. DEX-fill атомарен и **предполагается** на min-out/max-in границе; это не исторический фактический fill. CEX IOC может исполниться частично. Незакрытые позиции остаются с `net=None`, без фиктивного flat. Повторная recovery-заявка не использует уже потреблённую глубину того же snapshot. Источник, параметры и использованные данные сохраняются для воспроизведения.

Публичные данные могут использоваться только после их `received_at/observed_at`; будущие стаканы не подставляются. Котировка, устаревшая в ожидании DEX-подтверждения, не считается доказанным revert: результат остаётся UNKNOWN. Неизвестный остаток не выдаётся за ноль. Funding исключён из stress, результаты не добавляются в Ledger. Реальная DEX latency и зависимость от mempool/MEV/очереди не установлены моделью.

Firm quotes, газ, timestamps и native books записываются read-only Paper-источником. Quote history ограничена 100 000 строк и 72 часами. При редкой записи задержанные сценарии будут неполными. История не собирается без отдельно запущенного бота и настроенных источников. По умолчанию ранее сохранённые closed Paper позиции остаются доступны; отчёт проверяет их lineage, а отсутствие quote-history не подменяет ценой последнего mark.

## Wallet backend

Исправлена совместимость с обычной схемой 0x: API сообщает `buyTaxBps` и `sellTaxBps`, но отдельное `transferTaxBps` не является обязательным. При отсутствии этого поля оба token registry entries должны иметь отдельное подтверждение `transfer_tax_verified_zero=true`. Оно опирается на тот же точный expiring contract evidence, не выводится из символа или отсутствующего API-поля. Шаблон оставляет его false. Любой известный ненулевой tax по-прежнему блокирует маршрут.

`app/dex_wallet.py` реализует отдельную signing/broadcast boundary. Она не является стратегией и не вызывается main для автоматических swaps.

- Поддержаны только Ethereum mainnet, обычные ERC20, изолированный EOA и legacy `gasPrice`. Typed/EIP-1559 transactions, делегированные EOA, L2 fee semantics, tax/rebase tokens, native swaps, Permit2 и approvals не поддерживаются и не должны обходиться конфигурацией.
- `Provider.firm(..., execution_envelope=True)` после прежних read-only проверок возвращает ephemeral Envelope. Calldata/taker не входят в обычный JSON quote payload. Запрос Envelope ничего не подписывает и не отправляет.
- `Policy` требует явного enable, отдельного expiring acceptance, точных chain/wallet/venue/token/target scopes, raw sell caps и native gas cap. Шаблон `dex_wallet_acceptance.example.json` намеренно непригоден для допуска: нет адресов/evidence/сроков и все checks=false.
- Signer создаётся **только явно**, с ключом из обычного настроенного для такого процесса источника. Main не читает wallet key. Проверяются recovered signer, EIP-155 chain, nonce, gas, price, value, target и calldata подписанного raw transaction. Ключи и raw signatures не попадают в SQLite и экспорт.
- Для send необходим уже зарезервированный общий `live_trades` со strategy=cex_dex, тем же wallet/chain/venue и fingerprint. Entry ожидает PLANNED, exit — DEX_EXIT_SUBMITTING. Если эта запись не принадлежит CEX/DEX координатору, sender заблокирован.
- Два RPC должны совпасть по chain/latest/pending nonce; working nonce, другой chain, delegated wallet, старый/reorg block или gas spike блокируют подпись. `eth_call` повторяется перед signing. Target должен входить в allowlist; approve selector запрещён. Allowance должен быть заранее независимо проверен — backend его не создаёт.
- SQLite сохраняет SIGNING и уникальную пару chain/wallet/nonce, затем hash и BROADCASTING **до** сетевой отправки. Таймаут/ошибка/отмена оставляет UNKNOWN. Новый процесс делает lookup, не повторяет send, не увеличивает gas и не создаёт заменяющий nonce.
- Receipt должен совпасть по hash/nonce/call и быть в finalized canonical block обоих RPC. ERC20 transfers сверяются с parent/block token balances; native balance должен уменьшиться ровно на gasUsed × effectiveGasPrice. Nonce должен измениться ровно на один. Неожиданные движения кошелька блокируют доказательство. Exact-in подтверждает exact sold + min bought; exact-out — exact bought + max sold. Известный revert сохраняет фактический gas, не выдумывает token income.
- Данные двух RPC должны совпасть. Pending, отсутствующий receipt, смена block hash, конфликт событий или balances сохраняют HOLD. Успешный wallet receipt **не закрывает общий LIVE trade**, не подтверждает CEX hedge и не зачисляет PnL.

## Read-only наблюдение

При наличии `DEX_WALLET_RPC_PRIMARY` и `DEX_WALLET_RPC_SECONDARY` создаётся receipt observer. Нужны два различных HTTPS endpoint. Он наблюдает существующие intents даже при паузе сканера; подписей и approvals у него нет. Если остались unresolved wallet intents, а RPC-настройки удалены, startup блокируется. Совпадение двух URL не допускается; реальная независимость провайдеров подтверждается оператором, а не сравнением строк.

`/dex_wallet` показывает intents и подтверждённые raw cashflow/gas; `/readiness` отдельно показывает незавершённые блоки, unresolved LIVE, объём пригодной Paper-истории и действующее account evidence. Отчёт не снимает STOP и не меняет gates. Audit export включает wallet intents/events и quote/stress tables. Публичные ERC20 addresses/deltas сохраняются в экспорте; секреты и raw signing capabilities редактируются.

## Что ещё не закончено

1. Общий CEX/DEX LIVE-координатор: admit обеих площадок, время и доказательство CEX hedge относительно DEX receipt, симметричный выход, bounded one-leg recovery, фактическая fee/funding/gas attribution и финализация двух площадок. Сейчас wallet backend намеренно не подключён к автоматическим swap-сигналам.
2. Реальные account/chain/wallet certification и micro-canary на выбранном scope. Нельзя просто выставить все checks=true по успешным unit-тестам.
3. Длительная запись Paper/OOS и плотных публичных данных. Это нельзя выполнить мгновенно или восстановить будущими/выдуманными котировками.

Таким образом, software_complete=false и production_ready=false в текущем readiness. Проект не считается завершённым целиком.

## Проверки без реальных заявок

`python -m pytest tests/test_dex_wallet_backend.py tests/test_dex_execution_stress.py tests/test_cex_dex_roundtrip.py tests/test_dex_firm_simulation.py -q`

Тесты используют generated test keys, fake RPC и локальные публичные books. Они не читают финансовые ключи пользователя и не отправляют реальные транзакции. Отдельно нужны полный pytest, compileall/import main и CI того же commit.

Официальные контракты: [Ethereum JSON-RPC](https://ethereum.org/developers/docs/apis/json-rpc/), [eth-account](https://eth-account.readthedocs.io/en/stable/eth_account.html), [0x AllowanceHolder](https://docs.0x.org/evm/0x-swap-api/guides/swap-tokens-with-0x-swap-api). Разрешение spender не следует из `transaction.to`; Settler не должен получать approve.
