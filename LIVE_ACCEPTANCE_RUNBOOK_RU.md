# Финальная проверка CEX-исполнения

Код входа, наблюдения и выхода Futures/Futures и Spot/Futures связан в `app.main`. Этот документ описывает ещё не выполненную проверку на выбранных площадках. Локальные mocks не подтверждают биржевые semantics или прибыльность.

1. Зафиксировать commit и успешный CI. Собрать плотную Paper-историю, проверить полноту Replay и отложенный OOS. Сохранить отчёты, параметры, временные окна и идентификатор доказательств. Недостаточную выборку не принимать как успех.
2. На выделенных аккаунтах проверить отсутствие права вывода, используемый linear USDT контракт, one-way, native precision/limits, фактический fee tier, margin и funding history. Код не меняет leverage или режим аккаунта.
3. Проверить private order stream выбранных площадок: reconnect, late/duplicate fills, terminal updates, REST lookup по order/client ID. Проверить, что ошибки потока запрещают следующий вход и вызывают сверку; пустой поток не доказывает готовность.
4. В согласованной среде исполнения проверить IOC zero/partial/full fills, reduce-only, отмену остатка, unknown submit, потерю ответа, restart и one-leg recovery. Убедиться, что не появляется повторных intents/ордеров и false-flat. UNKNOWN и остатки без terminal/private согласованности требуют сверки. Для подтверждённых обоих остатков проверить bounded residual rounds: distinct IDs, cross-process claim, terminal предыдущих заявок, свежий private snapshot, прогресс fills, максимум 3 раунда; отсутствие прогресса и неопределённый результат запрещают повтор.
5. Проверить entry → actual fills → private позиции → выход → private-flat → funding settlement → итог. Учесть каждую комиссию и выплату ровно один раз; сопоставить с биржевым журналом. Защитный выход до commit RuntimeTrade также должен иметь terminal cashflow-доказательства.
6. После подтверждения пунктов создать рабочую запись по шаблону `live_acceptance.example.json`: evidence_id ссылается на сохранённые доказательства, venues содержит точные ID обеих площадок, verified_at/expires_at — Unix секунды, срок ≤24 часа. True означает выполненную проверку, а не желание включить функцию. Рабочая запись исключена из git.
7. Только после принятой проверки настроить `LIVE_ENABLED`, `LIVE_ENTRY_ENABLED`, `LIVE_EXIT_VENUES`, `PRIVATE_ORDER_STREAMS`, `LIVE_NO_WITHDRAW_ATTESTED`, `LIVE_CAPITAL_USD`, `LIVE_ACCEPTANCE_PATH`. Проверить `/startup`, `/live`, `/live_checks`, приватную сверку и STOP. `/live_checks` не снимает STOP и не отправляет заявки. Оператор снимает STOP явно; перезапуск возвращает STOP. Первое реальное исполнение — отдельный принятый micro-live запуск.

Лимиты Futures/Futures: одна общая durable позиция; бюджет ноги ≤5 USD и ≤10% LIVE-капитала; дневной realized loss limit 2%; свободный USDT баланс каждой площадки ≥120% бюджета ноги. Два выбранных аккаунта перед входом должны быть пустыми. Spot/Futures использует тот же общий слот/лимит, baseline существующего spot asset и запас USDT ≥240% бюджета в каждой account view, чтобы не использовать общий wallet дважды. Funding income при входе не зачисляется. IOC/market fallback и passive queue не включены в main. Spot/Spot, отдельная Funding-стратегия и CEX/DEX ещё не имеют подключённого write lifecycle.

В этой сессии шаги с реальными аккаунтами не выполнены, ключи и реальные заявки не использовались.

## Дополнительный scope Spot/Futures

На каждом выбранном physical venue отдельно подтвердить `defaultType=spot`, изоляцию/общий USDT wallet, полноту spot balance, нулевые/locked balances, IOC/client-ID lookup, base/quote fee units и реальный fee tier. В `strategies.spot_futures.venues.<venue>` шаблона acceptance все восемь checks должны ссылаться на проведённую проверку, а не включаться по наличию ключа.

Проверить spot-first zero/partial/full IOC → credited inventory → fresh native future hedge; истечение entry authority с независимо разрешённым защитным выходом; неизвестный ответ без blind resend; restart без повторения entry/exit; один общий durable слот с Futures/Futures. Проверить full/partial future close до продажи spot, резерв текущей base-комиссии, ограниченный явно вызванный recovery с новыми IDs и immutable baseline старого актива.

Сопоставить фактические cashflows, quote/base fees и funding с биржевыми выписками. Финальный residual inventory не flat: стоимость удержана из cash NET, asset valuation не превращается в прибыль, result/inventory/funding/events пишутся ровно один раз. Проверить зрелость funding и calendar-gap до TARGET/TRAILING, TIME_STOP при незрелом funding и повторный отказ при stale/private mismatch. Для clear kill нужны свежие чистые private/journal evidence; действие не снимает STOP.

Spot/Spot, отдельная live Funding-стратегия и CEX/DEX write lifecycle остаются следующими задачами. Настройка SF entry flag не включает их.
