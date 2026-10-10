# Фактическое состояние после интеграции рабочего запуска

Дата: 10 октября 2026. Рабочая ветка: `phase-2-discovery`.

## Текущий блок: автоматический CEX/DEX runtime

Проверено: 1255 тестов, compileall, импорт main и diff-check. Новые offline-сценарии покрывают configured bootstrap, одноразовые кандидаты без calldata в журнале, hedge, restart unwind, UNKNOWN/stale holds, повторную проверку target, funding gaps/NET stop/time stop и конкурентную сверку receipt.

Write-bootstrap подключён к secondary scanner и общему LIVE monitor. Одноразовые firm-кандидаты проходят повторный account/wallet admission; calldata остаётся в памяти. После finalized dual-RPC receipt новый цикл хеджирует фактический raw asset. После перезапуска незахеджированный остаток восстанавливается через новый bounded exit, без повторения входа. UNKNOWN удерживает owner и запрещает повторную отправку.

Paired NET mark использует обратный firm swap, свежий reduce-only CEX book, account fees, gas replacement valuation и только mature private funding. Target/trailing повторно проверяются перед выходом; time/NET stop и максимум 3 recovery rounds используют Session boundaries. Общий лимит освобождается после атомарного двухстороннего результата. Обычный PENDING с сохранённым hash получает WARNING; UNKNOWN, конфликт и слишком долгое ожидание сохраняют HIGH/STOP.

Код включается явными root LIVE и DEX_LIVE_ENABLED, проверенными registry/routes, изолированным ключом, caps и expiring CEX/wallet acceptance. Поддерживаемый wallet scope: Ethereum mainnet EOA, стандартные ERC20, USDT quote, legacy gasPrice; другие сети/типы транзакций не сертифицированы. Software components реализованы для этого scope; полная operational readiness остаётся false до настройки, реальной сертификации и Paper/OOS/micro-canary. Ни реальные ключи, ни реальные заявки при разработке не использовались.

## Предыдущий блок: durable CEX/DEX bridge API и общий read-only monitor

Проверка текущего блока: 1234 теста, compileall/import и diff-check. Только offline fixtures/моки, без реальных финансовых ключей или отправки сделок.

Реализованы `dex_live_bridge.Session` для обоих направлений, DEX receipt → actual-native CEX hedge, независимый reduce-only CEX exit → точное восстановление ERC20 inventory, максимум 3 явно запрошенных recovery rounds. Общая LIVE capacity сохраняется до private-flat и атомарного результата. Stage claim без terminal intent не считается нулевым исполнением; UNKNOWN не переотправляется. Добавлены конкретный SafeExecutor/CCXT backend, read-only admission с account/identity/margin/daily-loss/post-receipt NET проверками и final costs с mature private funding.

Результат пересобирается по двум durable journals; hash/receipt/CEX proof changes во время расчёта не допускают credit. Реальные ETH gas units сохраняются отдельно; USDT gas cost — явно отмеченная текущая executable replacement valuation, не выдуманный fill. Quote identity в конкретном costs/admission adapter ограничен mainnet USDT. Текущее dual-RPC inventory сверяется с последним receipt, включая nonce/native balance. Старые receipt payload без native balance proof требуют повторной операторской проверки и остаются HOLD.

Runtime read-only bridge observer зарегистрирован в secondary bootstrap и передан общему LIVE monitor. CEX leg получает своего exposure-owner, не трактуется как Futures/Futures; wallet leg не объявляется flat по одному CEX snapshot. Stage/event history входит в audit export. **Write-bootstrap и автоматический paired NET exit coordinator ещё не подключены**: main не создаёт DEX signer и не вызывает Session write methods. Поэтому software_complete/production_ready остаются false; реальные account/wallet certification и длительная Paper/OOS/micro-canary также впереди.

## Предыдущий блок: wallet boundary, receipts и DEX execution stress

Локальная проверка текущего блока: **1190 тестов passed**, `compileall`, импорт `app.main` и `git diff --check` успешны. Это проверка программного кода и моков, не сертификация реальных площадок или кошелька.

Добавлены isolated-wallet signer и проверка signed transaction, общий LIVE-owner/nonce claim, durable hash-before-broadcast и read-only dual-RPC finalized receipt accounting. Ethereum mainnet/ERC20/legacy gasPrice only; missing evidence остаётся HOLD, timeout не повторяется. Подписи и raw capability не входят в SQLite/экспорт. Полный автоматический CEX/DEX LIVE-координатор ещё не подключён, поэтому wallet receipt не означает закрытую двухстороннюю сделку.

Добавлены sequential DEX→CEX stress-сценарии, задержки, partial IOC, bounded recovery, revert и UNKNOWN. Будущие котировки не используются; уже потреблённая глубина не повторяется; неизвестный остаток не равен нулю. Результаты сценарные, funding исключён, Ledger не меняется. Quote history, runs/results и wallet intents/events доступны в audit export. Команды `/dex_stress`, `/dex_wallet`, `/readiness`; кнопки с выделением активного раздела.

Исправлена несовместимость с обычной 0x tax schema: отсутствующий transferTaxBps требует отдельного expiring transfer_tax_verified_zero evidence, не выдуманного нуля. Экспорт сохраняет валидные публичные token addresses/deltas, но редактирует signing capabilities и финансовые секреты.

Граница финала: **software_complete=false, production_ready=false**. Не завершены общий CEX/DEX LIVE hedge/exit/recovery/PnL bridge, реальные account/wallet certification и длительная Paper/OOS/micro-canary. В этой сессии реальные ключи и транзакции не использовались. Подробно: [DEX_WALLET_AND_STRESS_RUNBOOK_RU.md](DEX_WALLET_AND_STRESS_RUNBOOK_RU.md).

## Предыдущий блок: двусторонний CEX/DEX Paper и Replay

В main зарегистрированы LIVE Spot/Spot и Funding, strategy-scoped acceptance, общая capacity и монитор, Telegram read-only checks, close/recovery controls. Spot/Spot восстанавливает предварительно размещённые запасы по terminal/private cashflow, не использует transfers/borrow. Остаточные allocation deltas сохраняются отдельно; их наличие не считается flat. Funding прогноз не кредитуется; известные расходы каждой ноги резервируются отдельно, hold сохраняется до отправки, вывод результата требует mature private income. Цена/fee NET_STOP может работать без предполагаемого funding income.

Pair funding mark теперь сверяет maturity cutoff + calendar gap; наблюдатель больше не требует незрелый until=now private history для profit capture. Funding evidence берётся до свежих closing books. Добавлен операторский derivative exit по свежей сверке и атомарному claim. LIVE admission outcomes записываются в дневник.

CEX/DEX: 0x firm quote, exact units/min-received/tax/issues, expiring token/CEX identity registry, chain/block/hash/decimals/code/wallet/gas RPC proof и eth_call. Native CEX hedge и fee/gas NET ceiling model подключены к secondary scanner, Telegram и observations. Подписи, approvals и wallet transactions отсутствуют. Двусторонний DEX Paper/Replay реализован: вход и обратный выход имеют отдельные firm-котировки, exact-in min-out и exact-out max-in сохраняют точный raw inventory. Это модель исполнения по границам котировки; wallet LIVE и latency/partial-leg stress ещё не реализованы.

Локальная проверка этого блока: **1141 тест**, compileall, импорт main и diff check. Реальные аккаунты и транзакции не запускались. Инструкция: `REMAINING_LIVE_RUNBOOK_RU.md`.

### Двусторонний DEX Paper

- LONG DEX / SHORT CEX и предварительно размещённый SHORT DEX / LONG CEX. Обратный exact-out возвращает именно исходные raw units, forward продаёт весь min-out inventory, включая небольшой не захеджированный остаток. До входа проверяется обратный маршрут против существующего инвентаря кошелька. Модель не утверждает, что виртуальный вход изменил реальный баланс.
- Комиссия CEX берётся с аккаунта. Вход CEX оценён консервативным IOC limit, выход — полным native-contract VWAP; газ каждой операции оценивается отдельным свежим native ask. DEX fees включены в min/max quote cash и не вычитаются повторно. Прогноз funding не входит в доход.
- Один DEX цикл резервирует $12 из общего Paper-бюджета: максимум $5 на каждую сторону, комиссии, газ и запас. Резерв виден другим модулям во время сетевых запросов и удерживается при неполных данных. Новые входы отключаются настройкой стратегии; наблюдение продолжается.
- Исторические ставки единственной CEX-ноги сверяются с календарём, maturity и полнотой страницы; знак LONG/SHORT учитывается. Сетевой запрос истории предшествует свежим closing quotes. Settlement между историей и котировкой не считается покрытым.
- Time/NET stop фиксирует модель выхода; неполный funding оставляет EXIT_ACCOUNTING_PENDING. SQLite CAS атомарно сохраняет state/trades/marks/events/decisions и Ledger. Перезапуск удерживает резерв; повторное закрытие не повторяет доход. Удаление конфигурации при незавершённых DEX-позициях блокирует startup.
- `/dex_paper`, `/dex_replay`, кнопки, общий экран позиций и пять таблиц audit export. Replay принимает только CLOSED и полностью проверяемые marks: immutable entry, raw units, chain/block proof, свежие стаканы, fee/gas/funding lineage. Разреженная история исключается; train/test purged, история не даёт LIVE-допуска. Подбор правил Replay относится к time/trailing по NET marks, без симуляции задержки и частичных DEX fills.


## Предыдущий блок: Spot/Futures подключён к основному боту

Market-scoped spot private clients создаются отдельно от swap clients. Secondary scanner вызывает cash entry после сохранения observations и под общим monitor lock. Admission/gates требуют общих LIVE/ENTRY флагов, отдельного SF feature flag, свежей общей сверки, derivative private stream и дополнительных strategy/venue acceptance checks. Уже существующая конфигурация Futures/Futures не переписывается; его acceptance не разрешает SF.

Общий monitor владеет cash future по его реальному symbol/venue; спотовая нога не преобразуется в RuntimeTrade Futures/Futures. Cash observer сверяет terminal intents, фактический net credited asset и приватные позиции, затем строит свежий NET с актуальными account fees, worst spot limit и future closing VWAP. TARGET/TRAILING/NET_STOP требуют зрелого funding и отсутствия неподтверждённого settlement в последних 30 секундах; TIME_STOP не придумывает funding income. On-update диспетчер выполняет отдельно разрешённый выход, mature cash-result и уведомление ровно один раз.

Добавлены `/sf_live`, `/sf_checks`, залитые кнопки закрытия/явного bounded recovery и учёт остаточного актива. Новый SF-вход блокируется, если текущий spot balance не покрывает сохранённый inventory ledger. `/live_clear` снимает только monitor kill по свежей чистой сверке; STOP остаётся отдельным. UNKNOWN, stale/private mismatch, активный HIGH-инцидент и сторонний kill запрещают clear.

Проверено **1042 тестами**, compileall и diff check. В том числе сквозной scanner-dispatch → общая БД → общий monitor → dynamic exit → funding/result → inventory, scope/expiry acceptance, независимый защитный выход при истечении entry authority, factory scope, secondary hook, UI controls и запрет ложного Futures/Futures rebuild. Реальные аккаунты и сделки не использовались; certification/canary остаются невыполненными.

## Предыдущий блок (1010 тестов): cash-and-carry engine

Добавлены account preflight и durable sequential session для LONG_SPOT_SHORT_FUTURE: strict spot native/IOC quotes, реальные fee units, spot-first вход, свежий хедж по net credited asset, защита при partial/zero fill, отдельная exit authority, read-only restart и явно вызываемый recovery до 3 раундов. Общая SQLite reservation исключает одновременную сделку Futures/Futures. Actual market slippage, UNKNOWN, сторонняя fee currency, несогласованные private balances/контракты и недостаточная точность баланса удерживают цикл без слепой повторной заявки.

Spot/Futures public native-план **подключён к scanner, сохранённым observations и экрану рынка**. Различаются gross BASE, net BASE и native future contracts. Исправлен общий `/status`: он больше не пишет «LIVE отключён» при включённой конфигурации.

Финальный cash-result требует terminal journal, private proof и зрелого private funding. Cash NET не переоценивает оставшийся актив; `CLOSED_WITH_INVENTORY` и отдельная таблица inventory сохраняют его количество/стоимость без false-flat. Base-комиссия оценивается для attribution по фактической цене fill и не списывается дважды. Общие дневник/экспорт включают эти данные. Транзакция итог+inventory+funding+events защищена от повторного зачисления и смены журнала.

На этапе 1010 тестов Session ещё не вызывалась из main; эта связь реализована текущим блоком выше. Offline fake accounts и настоящие CCXT formatter не подтверждают реальное исполнение или доходность. Не заявляем завершённую сертификацию площадок.

Локально после блока: **1010 тестов**, compileall, diff check. API-ключи, реальные заявки и live-активация не использовались.

## Предыдущие этапы

Последний блок: bounded residual-exit coordinator подключён к main. Частичные остатки одной/обеих ног закрываются только при совпадении полного terminal cashflow и приватных native-контрактов. Round reservation атомарно сверяет fingerprint; stage IDs, terminal предыдущего раунда, фактический прогресс и максимум 3 раунда исключают слепые повторы. UNKNOWN, interruption, slippage, zero progress и лимит сохраняют hold. Добавлены read-only account/NET preview и `/live_checks`; явное снятие STOP требует свежей согласованной сверки. Защитный выход использует независимую exit authority. Terminal/private position lag допускает ожидание до 30 секунд без новых заявок/false-flat; после таймаута STOP. Исправлены false-flat/false-filled для очень малых количеств и invalid private data. Локально: 924 теста. Реальных заявок и сертификации площадок не было.

Предыдущий блок (844 теста): приватные order streams с durable событиями, REST fallback и пробуждением monitor; production IOC-вход Futures/Futures из сканера; атомарная single-position reservation; account/fees/funding/native/depth/margin проверки; expiring operator acceptance. Quote gate повторяется после claim intent, private-позиции с неверным объёмом не считаются flat. Protective terminal/private-flat цикл учитывается без RuntimeTrade. Локально: 844 теста. Live-ордера не отправлялись. Настройки запуска предусмотрены для входа и выхода после принятой проверки площадок; реализация входа больше не отсутствует.

Предыдущий блок (779 тестов): write-side автовыход Futures/Futures подключён к обновлениям monitor; atomic EXIT_SUBMITTING reservation, fresh book evidence, reduce-only SafeExecutor, известный one-leg residual recovery и сохранение UNKNOWN/partial/interrupted состояния. Итог закрытия остаётся у monitor после terminal/private-flat/funding проверки. Устаревшие marks не возвращают claimed exit в OPEN. Исправлена flat-проверка малых объёмов. Локально: 779 тестов. Явный LIVE_ENABLED + LIVE_EXIT_VENUES + снятый STOP разрешают настроенный выход; новые production-входы ещё не подключены. В этой сессии реальные заявки не отправлялись.

Предыдущий блок (758 тестов): единый проверяемый транспорт публичных стаканов для основных и вторичных сканеров; опциональные bounded CCXT Pro подписки, freshness/identity/depth/order проверки, инвалидация при сбоях и REST fallback; семплированная WS-история между циклами Futures/Futures, источник REST/WS в Replay; диагностика в Telegram и корректное завершение записи при shutdown. Локально: 758 тестов. PUBLIC_BOOK_STREAMS=false по умолчанию; переключение не разрешает LIVE. Private streams, сертификация площадок и автоматический lifecycle ещё не выполнены.

Предыдущий блок (722 теста): read-only восстановление уменьшенной пары по terminal fills и private-остатку; перенос realized recovery gross и всех расходов в marks/закрытие без двойного зачисления; полный cashflow-учёт закрытой сделки, включая односторонний abort; funding за весь исходный период; исходный notional для ROI; пересчёт модельной цели для меньшего остатка. Локально: 722 теста. Monitor подключён к main, отправка ордеров не добавлена. STOP не снимается. Account/position-mode/reduceOnly проверки, реальное execution-подключение и дооткрытие COMPLETE остаются следующими этапами.

Предыдущий блок (688 тестов): read-only сравнение FLATTEN/COMPLETE по общему и дополнительному NET, paid entry fees, taker вход/выход, funding allowance, slippage reserve, safety и break-even capture. Quote-backed incomplete entry использует reduce-only сокращение лишнего объёма и сохраняет его cashflow. Assessment не разрешает дооткрытие; reader и assessor в main не включены.

Предыдущий блок (658 тестов): опциональные свежие recovery quotes с native limits, полной контрактной глубиной и worst-level slippage; атомарное сохранение request/book evidence вместе с intent; повторная проверка gate/свежести после записи; export evidence; durable hold при actual slippage; транзакционная защита накопленных fills от запоздалого ответа отмены.

Предыдущий блок: terminal-проверка recovery orders, сохранение частичных аварийных fills/цен/комиссий, блокировка увеличения количества при округлении, проверка известных единиц приватной позиции и durable intents при приватном закрытии (611 тестов). Market requests без свежей reference price по-прежнему блокируются native-guard.

Это проверенный этап интеграции, **не финальный допуск к торговле**. Наличие вспомогательного модуля или успешного unit-теста не означает, что функция исполняется в основном боте. Ниже отражены реальные связи с `app.main`.

| Требование | Фактическое состояние |
|---|---|
| Futures ↔ Futures | Проверяемый REST + опциональный WS Discovery, Paper, дневник, проверенные marks, purged train/test и offline IOC/latency-проверка подключены |
| Spot ↔ Futures | Discovery, публичный fee-aware native-план, сохраняемый Paper и Replay подключены; short spot без borrowing не открывается |
| Spot ↔ Spot | Подключены сканер, сохраняемый inventory-aware Paper и Replay marks; вход требует виртуальных запасов, borrow/transfer не имитируются |
| Funding arbitrage | Сканер + сохраняемый Funding Paper: closing VWAP, историческая сверка ставок, pending accounting, Ledger, экспорт и Replay модели |
| CEX ↔ DEX | Price research + optional firm quote/RPC simulation + native CEX hedge/NET ceiling model; нет полного Paper/Replay или wallet LIVE |
| LIVE Futures ↔ Futures | IOC-вход из сканера, private streams, durable session и автовыход подключены; реальные аккаунты/исполнение не сертифицированы |
| LIVE Spot ↔ Futures | Account preflight, scanner entry, sequential session, общий monitor, dynamic exit, explicit recovery, atomic cash-result, Telegram и уведомления подключены; реальное исполнение не сертифицировано |
| LIVE Spot ↔ Spot | Sequential inventory-backed session, common monitor, dynamic exit, explicit recovery, atomic cashflow/result/allocation accounting, Telegram подключены; реальные аккаунты не сертифицированы |
| LIVE Funding | Общий derivative entry/exit lifecycle, отдельный hold/calendar/expense reserve и acceptance, private funding/results, ручной выход подключены; реальные аккаунты не сертифицированы |
| LIVE CEX/DEX | Wallet execution не реализован; read-only firm/RPC simulation не разрешает transactions |
| Восстановление | Непрерывный read-only monitor подключён к main: lookup UNKNOWN, terminal fills, fresh private snapshots, восстановление JSON из SQLite |
| STOP | Сохраняется; после каждого запуска STOP включён, снятие требует evidence |
| Частичные fills | Работающий остаток отменяется перед recovery; подтверждённые exit-остатки закрываются до 3 раундов; UNKNOWN/нет прогресса требуют сверки |
| Повторные ордера | Атомарная SQLite-резервация intent; повтор/UNKNOWN не отправляется вслепую |
| Закрытие | Подключён опциональный write-side reduce-only выход по сигналам; монитор фиксирует итог после private-flat, terminal exit fills и подтверждённого funding; запись результата и terminal-фазы атомарна |
| NET входа | Учитываются комиссии входа и консервативного taker-выхода; полный модельный блок существует отдельно |
| Funding в Paper-PnL | Прогноз будущего funding не зачисляется; нужны явно подтверждённые settlement-данные |
| Капитал | Общий текущий realized бюджет четырёх Paper-модулей; резерв обеих ног, входных затрат и запасов; итоговый капитал и дневной убыток восстанавливаются из закрытых позиций |
| Telegram | LIVE статус, позиции, инциденты и уведомления о подтверждённых закрытиях; рынок, стабильный выбор возможности, стратегии, Scan/Paper площадок, позиции, капитал, STOP, Startup, Replay, экспорт, уведомления о Paper-закрытиях |
| Дневник | LIVE marks, результаты, инциденты, funding settlements; наблюдения, причины Paper-входов/пропусков, позиции, marks, intents, execution events, durable состояния |
| Экспорт | XLSX + ZIP/CSV + JSON-вход для AI; до 50 000 строк на таблицу, секретные поля скрыты |

## Spot/Spot: запасы, сохранение и Replay

- `PAPER_SPOT_INVENTORY_JSON` задаёт **виртуальные** балансы. По умолчанию пусто: журнал показывает `BUY_QUOTE_LOW`/`SELL_BASE_LOW`, заявки Paper не открываются. Это не считывание реальных кошельков.
- На стороне покупки нужны USDT для фактического VWAP объёма, модельной комиссии и safety reserve. На стороне продажи нужен уже имеющийся базовый актив. Borrowing, transfer и гарантированный rebalance не предполагаются.
- Одна позиция модуля одновременно исключает повторное использование запасов. Вход меняет актив и USDT обеих площадок; выход продаёт купленное и восстанавливает исходный актив на площадке продажи. Если для обратной покупки не хватает USDT, позиция остаётся открытой с `EXIT_INVENTORY_LOW`.
- Вход, выход, балансы, marks и причины записываются одной SQLite-транзакцией. Версия состояния защищает от второго writer. После перезапуска запасы и ID восстанавливаются, смена исходного seed после распределения запрещена; пустой исследовательский счёт можно один раз наполнить через конфигурацию. Пустая конфигурация не стирает сохранённые запасы.
- Весь первоначальный reserve по заданным reference prices включён в общий Paper-бюджет с Futures/Futures и Spot/Futures. Начальная стоимость актива в запасе сама по себе не считается прибылью. Изменение рыночной цены постоянно удерживаемого seed-актива не отражено в NET отдельных round trips.
- Watch-запросы сохраняют исходные buy/sell площадки и base_qty, даже если лучший спред сменил направление или discovery отключён. Старые/неполные стаканы не используются для закрытия.
- Telegram: позиции и виртуальные запасы, третий экран Replay; экспорт: inventory state, сделки, marks и решения. Ledger восстанавливает закрытый NET после перезапуска.
- Spot/Spot Replay использует те же проверки состава затрат, хронологии, purged train/test и censored paths. Это моделирование записанных marks, без доказательства реальных fills/latency. Это исторический Paper-блок; текущий LIVE-контур описан выше.

## Telegram и Spot/Futures — текущая доработка

- Основные экраны получили русские названия и пояснения. Нативные стили кнопок: синий `primary`, зелёный `success`, красный `danger` для аварийного STOP. Цвет отображается поддерживающим Telegram-клиентом; оформление проверено на сериализуемых клавиатурах, без отправки сообщений пользователю из инструментов.
- Добавлены обновление экрана, возврат к рынку из детали, переход к LIVE-контролю из инцидентов/позиций, переключение между двумя Replay. На главной отображается одна актуальная кнопка паузы/возобновления.
- Исправлены runtime-падения Risk Center/главной: `unknown_orders` в supervisor — bool, а реальное поле RiskState — `consecutive_errors`.
- Пауза запрещает discovery и новые Paper-входы обеих основных стратегий, но watch-запросы и мониторинг открытых позиций продолжаются.
- Spot/Futures closing VWAP считается для сохранённого base_qty, а не нового объёма после изменения цены. Капитал резервируется по стоимости обеих ног.
- Marks хранят цену выхода, объём, gross, отдельные модельные комиссии входа/выхода, запас риска и подтверждённый settlement funding. Комиссия входа и запас фиксируются при открытии. Funding-прогноз не зачисляется.
- Новые позиции получают полный mark сразу при открытии; повторное открытие того же маршрута в цикле закрытия запрещено.
- Replay использует согласованный снимок SQLite и только пригодные истории. Legacy marks, неверный состав NET, конфликт объёмов, длинные разрывы и пересекающиеся train/test окна исключаются. Если правило не завершило путь до конца записанной истории, результат censored, а не выдуманный выход по последней цене.
- Правила выбираются только по train. Test не меняет выбранные параметры. Replay не включает LIVE и не меняет рабочие настройки.

## Непрерывное наблюдение и восстановление

- Read-only ордерный reader ищет exchange order ID или стабильный clientOrderId; отсутствие ответа не означает отказ ордера и не разрешает повторную отправку.
- Private snapshot получается после lookup и имеет время начала запроса. Старые данные, половина hedge, чужая экспозиция, конфликт владельцев и неправильное направление fills блокируют readiness и фиксируют STOP.
- Потерянный JSON восстанавливается по terminal fills, actual ценам, комиссиям, сохранённым contract sizes и совпавшей private-экспозиции.
- Выход наблюдается по свежему closing VWAP и account taker fees. UNKNOWN fee/funding сохраняется явно; time-stop и условные target/trailing — сигналы наблюдения, автоматических заявок здесь нет.
- Приватный funding history использует native signed cashflow Binance, BingX, Bitget, Bybit и OKX. Полная страница, неизвестная валюта/ID, конфликт события и незрелое окно остаются неподтверждёнными. Gate/MEXC не сертифицированы для этого collector.
- Итог закрытия сохраняется один раз в одной SQLite-транзакции с durable terminal и execution events. Пустая private-позиция без exit fills не доказывает итоговый PnL.
- Инциденты и funding events дедуплицируются; восстановившееся состояние не снимает STOP автоматически.
- Lifecycle останавливает monitor перед закрытием exchange clients. Экспорт включает новые audit-таблицы.

## Предыдущий блок интеграции

1. `LiveService` использует durable Session вместо изолированного RuntimeStore.
2. Метаданные и план сохраняются до первой отправки; actual fills и RuntimeTrade — до обновления JSON-кеша.
3. Entry recovery и close recovery используют `submit_intent`, поддерживаемый SafeExecutor.
4. Запрещены LIVE-входы без private snapshot; таймауты остаются UNKNOWN.
5. Реальный статус отменённого частично исполненного ордера больше не превращается в работающий PARTIAL.
6. Открытая Paper-позиция остаётся в наблюдении при исчезновении входного edge.
7. Spot/Futures контрактные объёмы стакана переводятся в единицы базового актива.
8. Spot/Futures позиции, marks и следующий ID восстанавливаются после перезапуска.
9. Запущены secondary loops; подключены настройки, выгрузки, OOS view и общий бюджет.
10. Основной lifecycle освобождает клиентов при ошибке startup и при остановке.
11. CCXT не выдаёт неизвестную комиссию или комиссию в BNB за нулевую USD-комиссию.
12. Длинные clientOrderId преобразуются в короткий стабильный идентификатор; lookup использует то же преобразование.


## Funding Paper и согласованное резервирование

- Сравнение funding разных интервалов приведено к общему горизонту. Прогноз входа считает календарь каждой ноги отдельно. Неизвестный интервал, несвежий календарь, несовместимый контракт или недостаточная глубина блокируют вход.
- Объём и маршрут сохраняются. Свежие closing VWAP используются для time-stop и ограничения basis loss. При недоступной истории ставок выход остаётся `EXIT_ACCOUNTING_PENDING`: цена выхода зафиксирована, капитал не освобождается, итог не попадает в Ledger.
- Исторические публичные ставки сверяются с календарём каждой ноги. Пропуски, переполненная страница, смена календаря, конфликт дублей и незрелое событие блокируют подтверждение. Одно событие не зачисляется повторно, включая дубли timestamp в пределах допуска.
- Funding cashflow здесь — **историческая ставка × reference notional входа** с направлением LONG/SHORT. Точная settlement mark price и выплата приватного аккаунта не подтверждаются этим Paper. Прогноз не зачисляется в NET.
- SQLite сохраняет состояние, сделки, marks, события и решения одной транзакцией с контролем версии. Перезапуск сохраняет reserve и зафиксированный выход; ошибка записи не меняет состояние в памяти. Закрытие добавляется в итог один раз.
- Добавлены `/funding_paper`, `/fund_replay`, Funding-кнопки, Ledger и пять таблиц экспорта. Replay принимает подтверждённые model marks, проверяет состав затрат и использует purged train/test. Недостаточная или разреженная история не выдаётся за положительный результат.
- Четыре Paper-модуля используют текущий восстановленный realized капитал. Futures/Futures, Spot/Futures и Funding резервируют стоимость обеих ног, входные комиссии и запас риска; Spot/Spot удерживает стоимость исходных запасов. Снижение бюджета запрещает новые входы, наблюдение существующих продолжается.
- Futures/Futures теперь восстанавливает base_qty и зафиксированные комиссии/запас, закрывающие котировки запрашиваются на сохранённый объём. Ошибка записи marks или закрытия сохраняет прежнее состояние в памяти; старый стакан не закрывает позицию.
- Legacy Futures/Futures Replay заменён проверяемым основным экраном в следующем блоке ниже. Ни один Replay не даёт допуска к AUTO.
- Финальная проверка этого блока: 498 тестов, compileall и проверка diff; ключи и реальные заявки не использовались.


## Проверяемый основной Replay и модель исполнения

- `/replay` теперь использует закрытые Futures/Futures позиции и полный JSON состава NET: объём, gross, комиссии, safety, funding и exit spread. Старые или конфликтующие marks, смена входных затрат, отрицательные затраты, разрывы и неверные временные окна исключаются. Вход получает первый mark атомарно с позицией; ошибка записи откатывает весь вход.
- Правила target/trailing/time-stop выбираются только на train, пересекающие границу позиции удаляются. Незавершённый путь остаётся censored без выхода по последней цене. Скалярные legacy-данные без абсолютных окон больше не объявляются проверенным split. Просадка считается по времени моделируемого выхода, одновременные закрытия агрегируются.
- Время цены стакана и время решения разделены. Paper открывается после получения исходных данных, а не задним числом по exchange timestamp. Последний mark восстанавливается после перезапуска; наблюдение с меньшим временем не меняет позицию. В одном цикле запрещён повторный вход по закрытой монете.
- Основной сканер сохраняет полученные **публичные REST/WS-стаканы в base units** с exchange timestamp, received_at и метаданными контракта. По умолчанию ограничение 200 000 строк и 72 часа; параметры есть в `.env.example`, запись можно отключить. Площадкам не отправляются ордера для записи.
- `/execution_replay` и кнопка в Replay запускают offline-проверку последних 100 закрытых Futures/Futures Paper-позиций: без задержки, с задержками 0.15/0.5 секунды и 1/1 секунды. Стакан берётся только из полученных к моменту виртуальной заявки; чтение будущих received_at запрещено.
- Модель — IOC/taker. Нехватка глубины создаёт частичное исполнение; неполный вход или запоздалый вход закрывается по последующему доступному стакану. Частичный выход пробует закрыть остаток. Отсутствующая ликвидность, старые данные или смена контракта сохраняют остаток и `net=None`, а не выдуманный итог.
- Все модельные комиссии учитываются отдельно для обеих площадок и каждой операции, включая recovery. Funding исключён из stress-модели, её результат не сравнивается напрямую с funding-inclusive Paper NET и не добавляется в Ledger.
- SQLite сохраняет run и результаты одной транзакцией. Результат включает параметры, исходную позицию и использованные стаканы; воспроизводимость проверена после очистки основной истории стаканов. Экспорт содержит market books, execution replay runs/results. Отчёты для AI сериализуются как строгий JSON без Infinity.
- **Границы модели:** REST/WS не доказывают фактический fill, очередь, доступность той же ликвидности, exchange-certified precision/limits или частный order stream. При редком REST-сборе проверки задержек часто остаются неполными. Поддерживается Futures/Futures, не DEX и не остальные стратегии; market IOC не заменяет будущий limit/market-гибрид.
- Проверено 544 тестами, compileall и diff check. Сценарии включают partial entry/exit, one-leg exposure, recovery failure, timing/lookahead, конфликт истории, сохранение доказательств, rollback и Telegram wiring. Ключи и реальные заявки не использовались.


## Native-план заявок и безопасный гибрид

- Futures/Futures scanner строит **публичный read-only native-план** по загруженным markets. Контракт должен быть явно linear USDT с известным положительным contractSize, native precision mode и опубликованным минимумом amount или cost. Неизвестный размер не подменяется единицей; некорректные рынки пропускаются.
- Общая base quantity округляется вниз до совместимого шага двух ног, проверяется native `amount_to_precision`. Поддерживаются TICK_SIZE, DECIMAL_PLACES и SIGNIFICANT_DIGITS; в неоднозначном случае план блокируется, не увеличивает исходную экспозицию. Цена проверяется штатным `price_to_precision`; изменение выше лимита запрещено. Ограничения amount/cost/price min/max проверяются отдельно.
- Scanner пересчитывает VWAP на native quantity. Маршрут и сохранённый объём открытой позиции не переокругляются для mark/exit. Непригодный native-план блокирует новые Paper-входы, но не наблюдение существующей экспозиции. План входит в payload наблюдений/решений и детали рынка в Telegram.
- CCXTExecutor перед create_order проверяет native quantity/price/limits. MARKET требует reference price и запрещает limit price/IOC-параметры. SafeExecutor отклоняет такой неверный запрос **до** claim ордерного намерения. Это дополнительная защита, не включение write authority.
- Ответы с NaN/Inf, overfill, неизвестной USD-комиссией или actual price не используются как подтверждённый fill. Cost/contracts больше не выдаётся за цену единицы base asset. Исполнение limit за пределами указанной цены остаётся неопределённым.
- Cancel/lookup обязаны сохранять cumulative fill, order ID и границы объёма. Уменьшившийся fill, чужой ID, overfill, неизвестная цена/комиссия блокируют дальнейшее исполнение. Подтверждённые canceled/rejected/expired состояния сохраняются terminal, а не превращаются в ACK/PARTIAL.
- `live_entry_flow.execute` получил **опциональный** свежий callback для IOC → market fallback. По умолчанию отсутствует; основной бот его не передаёт. Новый этап возможен только после двух terminal zero-fill IOC с нулевыми комиссиями, того же native quantity, свежего проверенного стакана, ограничения изменения цены и повторного NET/risk admission с taker fees.
- Market fallback получает отдельные durable intent IDs и stage metadata. UNKNOWN не пересылается; тот же trade_id после перезапуска ничего не отправляет. Два нулевых IOC и последующие фактические market fills восстанавливаются вместе по terminal evidence и private exposure.
- Рыночная цена исполнения не гарантирована. Actual fill сверх лимита slippage фиксирует durable hold/UNKNOWN и incident STOP; экспозиция остаётся видимой в read-only monitor. Это **не доказательство аварийного закрытия** и не автоматическое снятие STOP.
- Проверено 599 тестами, compileall и diff check. Новые тесты используют настоящие CCXT formatter на локальных markets, но не подключаются к биржам и не отправляют заявки.
- **До production:** native/public metadata не сертифицирует IOC/reduceOnly, position mode, private permissions, account fee tier или exchange-specific recovery. Для реальных recovery MARKET-запросов ещё надо подключить свежие reference quotes и venue-specific certification. Гибрид не включён в main/AUTO; полноценный passive-limit/market-гибрид и плотные order streams остаются отдельной задачей.

## До целевого финала ещё требуется

- Production CEX lifecycle: завершить проверку площадок и реального IOC-входа на выбранных аккаунтах. Production-entry admission подключён. Опциональный write-side автовыход и известный one-leg exit recovery со свежими reference quotes подключены к main; UNKNOWN и остатки без совпадающих terminal/private доказательств требуют дальнейшей сверки; подтверждённые остатки закрываются отдельным bounded координатором. Непрерывный read-only lookup, fresh snapshots, восстановление и exit-наблюдение уже подключены; разрешения на автоматическую торговлю они не дают.
- Опциональный публичный WS-транспорт подключён; private order streams подключены; ещё нужны certification snapshot/delta/sequence recovery каждой площадки и динамический приоритет подписок на весь меняющийся universe. Текущий sticky cap переводит overflow-пары на REST. Реальные подключения и плотная выборка в этой сессии не проверялись.
- Подтвердить полноту приватной funding history на каждой площадке и задержки settlement; завершить fee/slippage attribution и реальные account acceptance. Открытый funding до текущего времени остаётся незрелым и не разрешает target/trailing по подтверждённому NET.
- Spot/Futures replay уже работает по сохранённым model marks с purged train/test. Ещё нужны полноценная симуляция latency/fills, достаточная выборка, сквозное подтверждение Spot/Spot fills/latency и валидация Funding Paper на длительной записи публичных ставок и стаканов.
- CEX/DEX сопоставление контрактов, свежие gas-цены и исполнимые маршруты с min-received, tax/network evidence, simulation и отдельным failure acceptance. Индикативная price-заявка не заменяет эти проверки.
- Offline IOC/latency-модель Futures/Futures подключена. Ещё нужны плотные временные ряды, exchange-certified precision/limits, limit queue, private fills и распространение stress-проверок на другие стратегии; существующий Paper не является подтверждённой исторической доходностью.
- Подтверждённый CI, длительная Paper-выборка, OOS, затем отдельно принятый micro-live canary. AUTO не разблокируется количеством написанных файлов.

Реальные сделки в этой сессии не отправлялись. Тесты выполняются без финансовых ключей и реальных заявок. Настроенный write-side режим предназначен для отдельного запуска после проверки площадок.
