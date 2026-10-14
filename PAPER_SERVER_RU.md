# Перенос виртуального бота на Linux-сервер

Код для Docker Compose подготовлен; сервер в этой инструкции ещё не запущен.
Нужен Linux VPS с SSH, установленными Git, Docker Engine и Compose plugin.
Если старую историю переносить не нужно, используйте раздел «Новый эксперимент
с телефона» ниже: он явно создаёт отдельную базу, не отключая проверку её наличия.
Домен и входящие HTTP-порты не нужны: бот использует Telegram polling.
Сервер должен иметь исходящий доступ к выбранным биржам, 0x и Jupiter.

## 1. На Windows сохранить текущую историю

Остановить работающий бот Ctrl+C и дождаться возврата приглашения PowerShell.
Не запускать его повторно после переноса: Telegram допускает один polling-процесс
для токена, а две независимые копии истории создадут разные модельные результаты.

Из папки проекта:

```powershell
.\.venv\Scripts\python.exe -m app.prepare_paper_server --bot-stopped
.\.venv\Scripts\python.exe -m app.prepare_paper_server --verify server-transfer
```

Создаётся новая папка `server-transfer`: `bot.env` с вашими настройками/ключами,
SQLite online backup, runtime JSON (переключатели/STOP/режимы бирж), manifest
с контрольными суммами. Исходные файлы остаются на месте. Существующая папка
не перезаписывается; для нового снимка нужен другой `--output`.

**Не отправлять эту папку в Telegram или Git: в ней API-ключи и токен бота.**
Не добавлять приватный ключ кошелька для виртуального режима.
Открытая виртуальная позиция переносится вместе с резервом; исходный маршрут
должен оставаться в настройках. Перерыв рынка при переносе войдёт в модельную
историю: пропущенные движения нельзя считать исполненными сделками.

## 2. На сервере получить проект

После входа через SSH, из домашней папки пользователя:

```bash
git clone --branch phase-2-discovery https://github.com/mfievmkmkm/arbitrage-engine.git
cd arbitrage-engine
git rev-parse HEAD
```

Использовать ту же ревизию, что установлена на Windows. Если серверная папка уже
существует, сначала остановить прежний контейнер и сохранить его данные; не
перезаписывать его базу новой копией поверх работающего бота.

## 3. Передать снимок по SSH

В Windows PowerShell (USER и SERVER_IP заменить своими):

```powershell
scp -r .\server-transfer USER@SERVER_IP:~/arbitrage-engine/
```

На сервере:

```bash
cd ~/arbitrage-engine
chmod 700 server-transfer server-transfer/data
chmod 600 server-transfer/bot.env
docker compose -f compose.paper.yaml build
docker compose -f compose.paper.yaml run --rm --no-deps bot python -m app.prepare_paper_server --verify /transfer
docker compose -f compose.paper.yaml up -d
docker compose -f compose.paper.yaml ps
docker compose -f compose.paper.yaml logs --tail 80 bot
```

Проверка manifest нужна **до первого старта**: после работы база закономерно
изменится и прежние контрольные суммы больше не совпадут.
Настройки читаются из read-only файла `bot.env`, а база и состояния сохраняются
в `server-transfer/data` на хосте. Ключи не попадают в Docker-образ. Флаги LIVE
должны быть выключены; отдельный entrypoint проверяет это до запуска приложения.
При отсутствии базы запуск отказывается создавать пустую историю. Локальная
блокировка предотвращает два контейнера с одной и той же папкой данных; от
второго запуска на другом сервере/Windows она не защищает.

## 4. Проверить в Telegram

Открыть DEX → Paper → Обновить. Должны сохраниться капитал, позиции и история,
а возраст последней проверки должен обновляться. Если не меняется — посмотреть
логи и переключатели стратегий/паузу. Сохранённый STOP автоматически не сбрасывается.
Проверки в тестах не заменяют проверку доступности API именно с этого сервера.

После этого компьютер можно выключить: Docker запускает бота в фоне и
перезапускает при завершении процесса и перезагрузке Docker (`unless-stopped`).
Автоперезапуск не исправляет неверные ключи, недоступную биржу или отсутствие NET.

Полезные команды:

```bash
docker compose -f compose.paper.yaml logs -f --tail 100 bot
docker compose -f compose.paper.yaml stop
docker compose -f compose.paper.yaml up -d
docker compose -f compose.paper.yaml run --rm --no-deps bot python -m app.database_maintenance backup --db /data/arbitrage.sqlite3 --directory /data/backups
```

`logs -f` закрывается Ctrl+C без остановки контейнера. Для обновления кода:
остановить бот, сделать backup, обновить согласованную ревизию, затем
`docker compose -f compose.paper.yaml up -d --build`. Данные не удалять.
После изменения `bot.env` нужен `docker compose -f compose.paper.yaml restart bot`.

## Новый эксперимент с телефона, без старой базы

Старый бот на Windows должен быть остановлен. Создать VPS Ubuntu 24.04,
подключиться по SSH с телефона и установить Git/Docker/Compose по документации
провайдера. После клонирования проекта:

```bash
cd ~/arbitrage-engine
mkdir -p server-transfer/data
cp -n paper-server.env.example server-transfer/bot.env
chmod 700 server-transfer server-transfer/data
chmod 600 server-transfer/bot.env
nano server-transfer/bot.env
```

Обязательно заполнить BOT_TOKEN и ADMIN_ID (числовой Telegram ID). Для Ethereum
котировок нужен ZEROX_API_KEY, для Solana — JUPITER_API_KEY. BYBIT_API_KEY и
BYBIT_API_SECRET необязательны для виртуального режима; если ключи недоступны,
комиссии будут публичным допущением. Токен Telegram можно при необходимости
перевыпустить через BotFather: прежний токен тогда перестанет работать.
Не указывать seed/private key кошелька и не включать LIVE.

```bash
docker compose -f compose.paper.yaml build
docker compose -f compose.paper.yaml run --rm --no-deps bot python -m app.init_paper_server --fresh --bot-stopped
```

Инициализация разрешена только с двумя явными подтверждениями, проверяет настройки
и требует пустую папку данных. Повторный запуск не перезапишет базу. Создаётся
отдельный эксперимент с начальным капиталом PAPER_CAPITAL_USD; старые позиции,
резервы и результаты не переносятся. Обычный server entrypoint по-прежнему
отказывается запускаться без базы.

Если добавлен Jupiter ключ, расширить список монет **до запуска бота**:

```bash
docker compose -f compose.paper.yaml run --rm --no-deps -v "$PWD/server-transfer:/setup" bot python -m app.build_virtual_watchlist --env /setup/bot.env --preset channel --venues bybit,mexc --output /setup/virtual_watchlist_report.json --apply
docker compose -f compose.paper.yaml up -d
docker compose -f compose.paper.yaml logs --tail 80 bot
```

Дополнительный `/setup` — временная writable-копия того же bind mount для
помощника настройки; во время штатной работы `bot.env` смонтирован read-only.
Если Jupiter ключа ещё нет, пропустить команду сборки watchlist и запустить
только настроенный Ethereum маршрут после добавления 0x ключа.
Срок начала проверок/входов зависит от доступности API с сервера и NET, а не
от успешности Docker build. В Telegram проверить DEX → Paper → Обновить.
