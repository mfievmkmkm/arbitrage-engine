# Сверка исходного плана до кода · 10 октября 2026

## Актуальная матрица первой версии

Ниже — соответствие исходной концепции фактическому коду. «Реализовано»
означает наличие программного пути, а не сертификацию аккаунта, прибыльность
или доказанное исполнение на каждой бирже. Исторический аудит ниже сохранён
как контекст раннего этапа; его «not implemented / still required» не является
текущим списком отсутствующих модулей.

| Требование до кода | Текущий программный путь | Граница / оставшееся evidence |
|---|---|---|
| Futures CEX ↔ Futures CEX | `engine`, Paper/history, native admission, dispatch, durable entry/exit/recovery | Действующие сертификаты выбранных площадок, private history, canary |
| Spot ↔ Futures | scanner/Paper/history, `spot_future_live_session`, shared monitor и finalizer | Spot fee units, IOC/lookup, inventory и funding coverage на аккаунте |
| Spot ↔ Spot | prefunded inventory scanner/Paper/history, `spot_spot_live`, allocations | Нужен собственный inventory обеих площадок; перевод не является ногой сделки |
| Funding-arbitrage | scanner, `funding_paper_source`, rate windows, recorded execution, LIVE holding/exit | Public rates — модель; настоящий income требует private evidence |
| CEX ↔ DEX | firm simulation, token identity, native CEX hedge, durable wallet bridge, paired exit/recovery | Исполнение ограничено поддержанным mainnet/ERC20 scope; реальные RPC/кошелёк не проверены |
| Исполнимые стаканы, VWAP и NET | public/native books, fee/funding/gas reserves, preflight и post-fill guards | Котировка не является fill; неизвестные расходы не объявляются нулевыми |
| Реальные fills и частичное исполнение | native cumulative intents, private reconciliation, bounded hedge/flatten | UNKNOWN не считается нулём и не вызывает слепой повтор заявки |
| Market/limit-гибрид | aggressive IOC; FF zero-fill fallback с новым admission и отдельным acceptance | Passive maker queue не входит в выбранную первую версию и не обещает fill |
| Динамический выход | convergence/NET/time/adverse guards, strategy-specific exit coordinators | Полного схождения до нуля ждать не требуется; live rules не меняются из исследовательского отчёта |
| Дневник всех возможностей и пропусков | observations, strategy observations, `signal_decisions`, Paper marks, native orders/receipts, results | Оценка пропущенного сигнала не называется фактической упущенной прибылью |
| Replay/OOS/walk-forward | chronological single split плюс `walk_forward`: несколько purged expanding train/test периодов пяти стратегий | Нужна достаточная записанная история; censored/limited folds — неполная проверка |
| Задержки, одна нога, funding и DEX отказы | FF/cash/funding recorded-book stress и DEX receipt/revert/UNKNOWN scenarios | Stress — модель, не независимое операционное испытание |
| Фактические затраты и проскальзывание | `live_execution_costs`, native cash/DEX attribution; receipt против sealed firm-price reference | Старый exact-out без ожидаемого input не получает выдуманный reference из max input |
| Капитал, risk, остановка и восстановление | persistent Paper/verified LIVE accounting, shared capacity, STOP/kill/private recovery | Малый бюджет и лимиты не расширяются автоматически; inventory не false-flat |
| Биржи и переключатели | configured extensible venues, persistent strategy/venue controls | «Все биржи» — расширяемость, не утверждение о проверенной поддержке любого API |
| Русский Telegram, уведомления и экспорт | strategy/position/risk/capital/venue/DEX consoles, lifecycle alerts, XLSX/CSV и AI report input | Сквозная проверка бот-сессии и доставки выполняется отдельно |
| Ребаланс и AI | manual recommendation foundations, structured reports, recommendation-only governance | Автопереводы и внешняя AI-модель не обязательны для первой версии; отчёты не дают execution authority |
| Сохранность данных | `database_maintenance`: online WAL-aware backup, manifest verification, non-overwriting recovery copy; backup-before-retention | Восстановленная копия inspection-only; startup заблокирован до отдельной операторской сверки |

## Что ещё нельзя закрыть написанием кода

1. Scope/account/wallet certification на реальных выбранных площадках.
2. Длительная плотная Paper-история, достаточное OOS/walk-forward и эксплуатационные прогоны.
3. Отдельно разрешённый минимальный реальный canary и сверка обеих ног/балансов.
4. Операторское принятие релиза и конкретных параметров; zero unresolved exposure.

Проценты готовности, число тестов и SHA отчёта не заменяют эти пункты.
Программные additions, заранее названные последним блоком (DEX reference
attribution, multi-fold walk-forward, database backup/retention и актуальная
сверка ТЗ), реализованы. Это не доказательство, что дальнейший эксплуатационный
аудит не обнаружит ошибок. Resting limit queues, auto-withdrawals и внешнее AI
исполнение не добавляются для увеличения числа функций.

## Исторический аудит ранней сборки — НЕ текущий backlog

This document maps the implementation to the design agreed before coding.

## Core strategy order
1. CEX Futures <-> CEX Futures — implemented through Discovery, Paper, Replay and fail-closed micro-live foundations.
2. Spot <-> Futures — Discovery/Paper/Replay pipeline under integration; LIVE locked.
3. Spot CEX <-> Spot CEX — planned, not implemented yet.
4. CEX <-> DEX — safety/quote/paper foundations; no wallet execution.
5. Funding arbitrage — funding is modeled in existing strategies; standalone funding strategy not implemented yet.

## Required economics
- RAW spread — implemented.
- Executable spread / order-book VWAP — implemented.
- NET after fees/funding/slippage/safety — implemented in core; strategy-specific models exist.
- Actual fills define real entry — implemented in live lifecycle.
- Funding interval/timing — implemented fail-closed when unknown.
- DEX gas / fee / impact / slippage separated — foundations implemented.

## Execution invariants
- no blind dual MARKET assumption
- persisted order intents
- deterministic client IDs
- partial-fill handling
- one-leg recovery
- UNKNOWN submit reconciliation
- private position verification
- close only after private exposure=0
- reduce-only futures closes
- crash/restart policy
- global/venue/pair kill switches
- operator STOP / evidence-gated RESUME
Implemented in Stage 5 foundations.

## Exit engine
Implemented foundations:
- convergence
- NET trailing
- time stop
- adverse conditions
- opportunity cost
Still to complete: unified production policy using replay-selected parameters and optional partial exits only if Replay proves value.

## Discovery / Diary / Replay
Implemented:
- opportunity collection
- Paper
- marks
- Replay
- execution events
- order intents
- strategy observations
Still to complete:
- richer skipped-signal replay
- full CSV/XLSX export
- parameter grid/out-of-sample automation
- long-run venue-pair reports.

## Capital / risk
Implemented:
- small-bank micro-live budget
- 1x initial leverage
- daily stop
- drawdown stop
- max open trades
- portfolio exposure
- venue ranking
- manual rebalance recommendation
Still to complete:
- persistent bankroll ledger
- realized funding settlement ledger
- venue allocation UI.

## Telegram
Implemented: Discovery/Paper/Diary/Replay/Risk/Startup/LIVE controls.
Still to complete:
- unified multi-strategy dashboard
- active live position edited message
- Capital / DEX / Analytics / AI pages
- venue Scan/Paper/Real toggles
- dedicated STOP confirmation UX.

## DEX
Implemented safety architecture only.
Still required:
- real aggregator adapters
- chain/token metadata providers
- deposit/network mapping
- route freshness
- wallet transaction simulation
- approvals/nonces/reorg handling
- dedicated DEX Replay/failure acceptance
- isolated wallet execution only after acceptance.

## AI
Implemented governance foundations:
AI -> recommendation -> Replay validation -> operator approval.
Still required:
- report generation from aggregates/anomalies
- recommendation history
- no direct execution authority (must remain invariant).

## AUTO
Eligibility foundations exist but AUTO remains locked.
Required before final AUTO:
- operational venue evidence
- statistically sufficient Paper/Replay
- verified micro-live history
- canary pass
- zero unresolved incidents/orders
- explicit operator approval.

## Important omissions caught by this audit
These are NOT forgotten and must be built before final completion:
1. Spot CEX <-> Spot CEX strategy.
2. Standalone funding-arbitrage scanner.
3. Full export/reporting.
4. Skipped-signal replay.
5. Persistent bankroll/funding ledger.
6. Telegram multi-strategy + capital/DEX/AI UI.
7. Real DEX provider adapters and chain execution safety.
8. Production unified Exit policy and Replay parameter promotion.
9. Venue-specific operational certification.
10. Rebalance analytics/history.

The implementation must continue in the original strategy order and may not enable a later strategy's LIVE mode before its own acceptance gate.
