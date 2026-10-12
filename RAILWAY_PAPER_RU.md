# Новый виртуальный эксперимент на Railway с телефона

Код подготовлен; реальный сервис нужно создать в своём Railway аккаунте.
Trial — кредит на использование, не гарантия 30 дней непрерывной работы.
Нужен Full Trial для доступа к биржам; на Limited Trial сеть ограничена.

1. Railway → New Project → Deploy from GitHub repo → `mfievmkmkm/arbitrage-engine`.
2. В Source выбрать ветку `phase-2-discovery` и последнюю согласованную ревизию.
   Если репозиторий не виден, проверить доступ GitHub integration к этому репо.
3. Добавить Volume, подключить к сервису бота, Mount Path **`/data`**.
4. В Variables → Raw Editor добавить значения ниже, заменив placeholders.

```dotenv
BOT_TOKEN=свой_токен
ADMIN_ID=свой_числовой_id
ZEROX_API_KEY=свой_ключ_0x
JUPITER_API_KEY=свой_ключ_Jupiter
EXCHANGES=bybit,mexc
PAPER_CAPITAL_USD=50
LIVE_ENABLED=false
LIVE_ENTRY_ENABLED=false
DEX_LIVE_ENABLED=false
DEX_VIRTUAL_PAPER_ENABLED=true
PAPER_SERVER_FRESH_START=true
DEX_VIRTUAL_WATCHLIST_PRESET=channel
DEX_VIRTUAL_SCAN_INTERVAL_SEC=5
BOOK_HISTORY_MAX_ROWS=20000
BOOK_HISTORY_HOURS=24
```

Не добавлять вручную RAILWAY_VOLUME_MOUNT_PATH: его задаёт настоящий Volume.
DB_PATH и JSON маршрутов вручную не нужны: entrypoint использует `/data/paper`.
При наличии уже сохранённой базы она не сбрасывается, даже с fresh-флагом true.
Если папка данных непустая, но базы нет, новый старт откажется затирать состояние.

BYBIT_API_KEY и BYBIT_API_SECRET необязательны. Без них комиссии CEX — публичное
допущение; с read-only ключом Bybit — прочитанные аккаунтные ставки. Ключ MEXC
для этого эксперимента не требуется. Приватный ключ кошелька не нужен.

Если Jupiter ключ пока недоступен, не добавлять пустой placeholder и поставить
DEX_VIRTUAL_WATCHLIST_PRESET=none: будет только Ethereum WETH/Bybit. Для него
ZEROX_API_KEY обязателен. Для Solana вернуть channel и добавить Jupiter ключ.

5. В Settings → Deploy указать Start Command:
   `python -m app.railway_paper_server`. Не назначать pre-deploy и HTTP healthcheck:
   Volume появляется во время запуска, а бот не предоставляет HTTP сервер.
   Serverless/Sleep выключить. Один экземпляр сервиса; домен не требуется.
6. Deploy. В логах первый раз появится NEW virtual experiment, затем результат
   сборки доступных маршрутов и запуск polling. При перезапуске база и список
   маршрутов сохранятся. Секреты в логах не печатаются.
7. После первого успешного запуска поменять PAPER_SERVER_FRESH_START=false.
   Это защищает от создания новой базы, если Volume позже случайно отсоединят.
8. Telegram → DEX → Paper → Обновить: проверить возраст последней проверки,
   число маршрутов и причины отказов. Старый Windows-бот должен быть остановлен.

Jupiter запросы ограничены AMM роутером Metis через официальный excludeRouters.
Проверяются routePlan, min-out, заданный slippage и price impact; другие типы
котировок не преобразуются в подтверждённое исполнение.

Сборка списка при первом старте проверяет точные mint и публичные CEX markets,
пишет причины пропусков и сохраняет найденные маршруты на Volume. При следующих
стартах повторно не меняет токены/decimals открытых позиций. Если все котировки
Solana отклонены, журнал сообщает причины и продолжает уже настроенные маршруты.
Для явной повторной сборки добавить DEX_VIRTUAL_WATCHLIST_REVISION=2 (при следующем
обновлении списка — другое значение). Старые маршруты сохраняются, новые только
добавляются после проверок. Ошибочная refresh-попытка не помечается успешной.

На Trial следить за RAM, расходом кредитов и размером Volume (0.5 ГБ). Ограничение
истории стаканов не ограничивает весь журнал: позиции/решения/marks продолжают
накапливаться. Выгружать дневник и делать backup до окончания пробного периода.
После удаления Volume история не восстанавливается из репозитория.

Документация: https://docs.railway.com/volumes и https://docs.railway.com/pricing/free-trial
