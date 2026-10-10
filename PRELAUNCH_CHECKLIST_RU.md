# Домашняя проверка первой версии

Рабочая ветка: `phase-2-discovery`. `main` пока содержит старый MVP.
Не меняйте торговые флаги и лимиты ради прохождения проверки. Этот файл не даёт
разрешения на реальные заявки или переводы.

## 1. Локальная сборка — без аккаунтов

В каталоге проекта с установленными requirements и pytest:

```bash
python -m pytest -q
python -m compileall -q app
```

Не запускайте `python -m app.main` вслепую: он читает существующую конфигурацию,
которая может содержать разрешения LIVE. Сначала отдельно проверьте выбранный
режим, текущие STOP/acceptance и scope подключений. Тесты используют offline
fixtures; синтетические ключи в тестах не являются вашим кошельком.

## 2. Аудит уже существующей базы

Если DB_PATH отличается, замените `arbitrage.sqlite3` фактическим путём.
Эти команды не создают отсутствующую базу, не создают клиентов и ничего не торгуют:

```bash
python -m app.project_readiness --db arbitrage.sqlite3
python -m app.walk_forward --db arbitrage.sqlite3
```

Чтобы проверить scope сертификата CEX, добавьте к readiness фактический путь
`--acceptance live_acceptance.json --venues binance bybit` либо свои площадки.
Example JSON с false не является действующим сертификатом. Wallet certification
проверяется отдельно. Empty/insufficient history — ожидаемый результат, не
причина подделывать marks, fees, receipts или положительные checks.

Walk-forward требует истории разных временных окон. По умолчанию initial train
20 позиций, следующие test участки по 10, минимум 10 после purge, максимум 20
периодов. Это настройки исследования, не доказательство статистической
достаточности. Fold results не меняют exit policy или acceptance автоматически.
История ограничена 10000 позиций, 10000 marks на позицию и 500000 marks на
стратегию в одном запросе. Превышение явно исключает выборку, а не делает
частичный набор успешным. Подбор правил выполняется вне Telegram event loop,
чтобы исследовательский запрос не занимал его синхронным расчётом.

## 3. Полная резервная копия базы

```bash
python -m app.database_maintenance backup --db arbitrage.sqlite3 --directory backups
```

Команда выдаст уникальное имя файла и путь manifest. Укажите ровно возвращённое
имя для проверки; не выбирайте файл случайным wildcard:

```bash
python -m app.database_maintenance verify --backup backups/ИМЯ_ИЗ_ОТЧЁТА.sqlite3
```

Backup учитывает committed WAL и проверяет SQLite integrity. Это полная база,
включая private financial history, а не отредактированный публичный отчёт.
Файл и manifest имеют ограниченные права. SHA подтверждает целостность копии,
не её прибыльность, независимое происхождение или подпись оператора.

Не публикуйте backup и конфигурацию. `.env`, runtime JSON/STOP и внешние
сертификаты не входят в database backup: их хранение и scope проверяются
отдельно. Код не извлекает и не архивирует ваши credentials.

## 4. Проверка восстановления — только отдельная inspection-копия

```bash
python -m app.database_maintenance restore-copy --backup backups/ИМЯ_ИЗ_ОТЧЁТА.sqlite3 --destination recovery-inspection.sqlite3
python -m app.project_readiness --db recovery-inspection.sqlite3
```

Рабочая база не заменяется. Существующее имя и sidecars вызывают отказ.
Восстановленная копия содержит marker; runtime main блокирует её запуск до
отдельной операторской reconciliation. Это предотвращает слепое исполнение
старых intents/nonce. Нет кнопки «снять marker и торговать»: восстановление
реального сервиса требует свежей private сверки и отдельного решения оператора.
Для диагностики используйте read-only аудит, не подменяйте DB_PATH этой копией.

## 5. Очистка — сначала предварительный просмотр

```bash
python -m app.retention --db arbitrage.sqlite3 --observation-days 30
```

По умолчанию ничего не удаляется. Если после просмотра действительно нужна
очистка старых scanner observations, отдельная явная команда:

```bash
python -m app.retention --db arbitrage.sqlite3 --observation-days 30 --apply --backup-directory backups
```

Сначала создаётся и проверяется полный backup. Удаляются только старые строки,
точно совпадающие с сохранёнными; concurrent inserts/edits остаются. Журналы
заявок, receipts, execution events, funding, Paper marks, решения и результаты
не удаляются. Backup остаётся для восстановления удалённых observations.
Без `--apply` либо без успешного backup удаление невозможно.

## 6. Затем эксплуатационная проверка

- Бот: доступ admin-only, русский интерфейс, обновление карточек, STOP/Resume,
  уведомления и XLSX/CSV; `/readiness`, `/walk_forward`, `/live_costs`.
- Площадки: actual fees/units, min notional, client ID, IOC/reduce-only, lookup,
  private positions/orders/funding completeness и отсутствие withdrawal scope.
- DEX: token/network identity, два RPC, isolated wallet, preapproved allowance,
  газ, nonce/finality, receipt cashflow и expected firm-price evidence.
- Записанная Paper/OOS/walk-forward история и отказные runtime-сценарии.
- Только после отдельного допуска — минимальный real canary с независимой
  сверкой обеих ног и итоговых балансов. Успешные offline tests его не заменяют.

Не выключайте protective gates и не увеличивайте риск ради наличия сигналов.
Система не может обещать прибыль или идеальную работу только по числу тестов.
