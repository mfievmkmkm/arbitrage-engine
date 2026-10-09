# Spot/Spot, Funding и DEX: подключение и проверка

Этот документ описывает реализованные связи с `app.main`, а не выдаёт допуск к реальным аккаунтам. Общие LIVE/ENTRY, STOP, kill, выбранные площадки, no-withdraw и свежая приватная сверка продолжают действовать. Новые strategy feature flags по умолчанию включены, но не заменяют отдельную запись acceptance.

## Spot/Spot

`/ss_live` показывает изменение запаса по каждой бирже, `/ss_checks` проводит проверку без заявок. Для входа нужны USDT на бирже покупки, заранее размещённый BASE на бирже продажи и USDT для обратного восстановления. Нет перевода средств в середине сделки и нет займа.

1. Загрузить отдельные spot private clients, подтвердить точные USDT-инструменты, native precision/minimum, lookup, IOC и комиссии в USDT/BASE.
2. В `live_acceptance.json` заполнить общие проверки и отдельные `strategies.spot_spot.venues` для обеих бирж. Все `SPOT_CHECKS` и `inventory_restoration` обязательны. Старый Futures/Futures или Spot/Futures допуск не разрешает новую стратегию.
3. Проверить только `/ss_checks`: запас, quote buffer, актуальные account fees, свежая полная глубина и NET после четырёх комиссий и запаса риска. Максимальный notional обеих ног ограничен $5 и 10% капитала; дневной лимит — 2%.
4. Контрольный цикл: buy IOC → terminal + private proof → sell IOC только в пределах фактически полученного актива и имеющегося запаса → `SS_OPEN`.
5. Выход: восстановить проданный запас, затем продать принадлежащий циклу кредит. Partial restoration останавливает продолжение. UNKNOWN не разрешает повтор, даже после перезапуска.
6. Явное recovery доступно только после terminal lookup и точной сверки базового и USDT балансов. Не более трёх отдельных раундов с новыми intent IDs и атомарным claim.
7. Финальный NET содержит реальные движения USDT. Остаточный положительный актив оценён в ноль, малый дефицит дополнительно списан по reference cost. Такие циклы получают `CLOSED_WITH_INVENTORY`, не ложный flat. Изменения распределения сохраняются в `live_spot_allocations`, результате и экспорте.

Проверить zero/partial fill, потерянный ACK, изменения комиссии, другой fee token, transfer/баланс вне цикла, конкурирующие процессы, crash после stage claim и закрытие при отключённом входе. Отдельные LIVE gates запрещают отправку при STOP/kill. Кнопка восстановления не снимает STOP.

## Funding LIVE

`LIVE_FUNDING_ENABLED=true`; срок `LIVE_FUNDING_HOLD_SECONDS` по умолчанию 28800 секунд, ограничен 60–86400. `LIVE_FUNDING_MIN_CARRY_PCT=0.03`. `/funding_live` и `/funding_checks` дают статус и проверку без заявок.

В `strategies.funding_arb.venues` требуются `settlement_calendar`, `private_income`, `holding_exit`, `spread_stop` для каждой биржи, плюс общий derivative acceptance и наблюдаемые private order streams. Поддерживаются только адаптеры с известной семантикой private income в `private_funding_reader.POLICY`.

Календарь проверяется отдельно для каждой ноги на реальном горизонте удержания; вход ближе 60 секунд к settlement не допускается. Положительный прогноз выбирает направление, но не финансирует отрицательный NET входа. Отдельно резервируются известные расходы каждой ноги — ожидаемый доход их не компенсирует. Это консервативное ограничение для малого капитала: часть чисто carry-сделок намеренно не открывается.

Стратегия использует общий проверенный derivative entry/partial recovery/exit/final accounting. Strategy + календарь записываются до первого ордера и сохраняются при restart. Funding hold не подменяется коротким Futures/Futures timeout. Успешное сжатие спреда может закрыть позицию раньше settlement; ждём достаточный фактический NET, а не обязательную выплату.

Открытый NET использует private history до maturity cutoff и проверку отсутствия более нового settlement по публичному календарю. Calendar gap блокирует profit capture. Консервативный price/fee NET_STOP в 1% стартового капитала и TIME_STOP не используют прогнозируемый доход. Ручной выход использует свежую общую сверку, свежую closing quote и atomic EXIT_SUBMITTING reservation. Финал ждёт зрелую history до private-flat.

## CEX/DEX firm quote и RPC simulation

По умолчанию остался read-only price research. Для дополнительной симуляции задать `DEX_SIMULATION_ROUTES_JSON`, `DEX_SIMULATION_RPC_URL` (HTTPS), `DEX_TOKEN_REGISTRY_PATH` и `ZEROX_API_KEY`. При наличии simulation routes именно они используются CEX/DEX сервисом. Эти настройки не создают signer и не отправляют approvals/заявки/транзакции.

Пример маршрута с **фиктивными адресами**, которые нужно заменить подтверждёнными:

```json
[{"chain_id":1,"sell_token":"0x1111111111111111111111111111111111111111","buy_token":"0x2222222222222222222222222222222222222222","sell_amount_raw":"4000000","taker":"0x4444444444444444444444444444444444444444","label":"USDT → X / CEX short"}]
```

`dex_token_registry.example.json` не является сертификацией: его timestamps, evidence и разрешения не заполнены. Registry действует максимум сутки. Нужны проверенные точные chain/address/decimals, принадлежность quote USDT, соответствие asset CEX base/symbol, сеть и allowlist swap call targets. Наличие bytecode не заменяет аудит контракта; RPC подтверждает только существование кода и decimals.

Provider проверяет exact-in amount, `minBuyAmount`, отсутствие allowance/balance/API simulation issues, нулевые известные buy/sell/transfer taxes, calldata/target/value, газ, RPC chain, свежий block/hash, native и token balance. `eth_call` выполняется на том же блоке; смена hash блокирует доказательство. Сырой calldata, wallet, RPC URL и API key не входят в дневник. Идентификатор котировки — hash её scope и содержимого.

CEX сопоставление использует native linear USDT contract sizing, свежую executable IOC depth и реальные account taker fees. Газ оценён по свежему CEX ask native asset. Допускается до $5 notional, не более $0.05 несогласованного объёма и $0.25 gas. NET — **модель потолка полного схождения**, с двойным gas/exit fee reserve. Это не исполненный цикл, не гарантированная прибыль и не результат Paper/LIVE Ledger. `simulation_allowed` отделён от `paper_allowed`/`live_allowed`.

До полноценного CEX/DEX Paper/Replay и wallet LIVE остаются двухсторонний exit route, реальная DEX/CEX latency/partial-leg модель, allowance/signing boundary, private receipt/event accounting и отдельная chain/wallet certification. Требования не считаются пройденными по числу тестов.

Официальные API-контракты: [firm quote](https://docs.0x.org/api-reference/evm-ap-is/swap/allowanceholder-getquote), [issues](https://docs.0x.org/docs/introduction/api-issues).
