# Arbitrage Engine

Русскоязычный Telegram-интерфейс исследования межбиржевого арбитража.
Рабочая версия находится в **`phase-2-discovery`**; `main` содержит старый Discovery MVP.

Текущий этап: интегрированный Discovery/Paper/Replay. **Автоматическая реальная торговля и AUTO ещё не выпущены.** Фактические возможности и незавершённые пункты описаны в [RELEASE_STATUS_RU.md](RELEASE_STATUS_RU.md).

## Работающий запуск

`app.main` запускает Futures/Futures scanner и Paper, Spot/Futures scanner и сохраняемый Paper, Spot/Spot scanner с сохраняемыми виртуальными запасами и Funding scanner с сохраняемым Paper. Для DEX доступен опциональный 0x price-research; кошелёк не подписывает и не отправляет транзакции.

Telegram: `/start`, `/top`, `/paper`, `/funding_paper`, `/fund_replay`, `/strategies`, `/exchanges`, `/diary`, `/replay`, `/execution_replay`, `/capital`, `/risk`, `/startup`, `/live`, `/live_stop`, `/live_resume`, `/export`, `/pause`, `/resume`.

Основной `/replay` использует проверенные Paper marks и purged train/test. `/execution_replay` проверяет задержки, partial fills и закрытие остатка по записанным публичным REST-стаканам. Незавершённое исполнение не получает итоговый NET. Это offline-модель IOC/taker, а не реальные fills; funding из этой проверки исключён, результат не меняет капитал.

Futures/Futures получает read-only native-план: одинаковая экспозиция двух ног, CCXT-округление количества/цены и проверка опубликованных лимитов. Неизвестные параметры блокируют новые Paper-входы, существующие позиции продолжают наблюдаться. Публичная проверка не подтверждает права аккаунта или готовность LIVE.

Опциональный IOC → market fallback в execution flow выключен по умолчанию и не подключён к основному боту: он допускается только после двух terminal zero-fill ответов, свежего native-плана и повторной проверки NET. UNKNOWN и partial fills не разрешают повторную отправку.

Стаканы записываются в основном сканере с временами котировки и получения. По умолчанию хранятся до 200 000 строк / 72 часов; настройка `RECORD_PUBLIC_BOOKS`, `BOOK_HISTORY_MAX_ROWS`, `BOOK_HISTORY_HOURS` в `.env.example`. Использованные доказательства сохраняются внутри результатов исполнения.

В `/export` формируются XLSX и ZIP с CSV-таблицами и JSON-входом для анализа. Replay выбирает параметры на train и отдельно показывает отложенную выборку; параметры автоматически не меняются.

## Разработка и проверка

Python 3.11+. `pip install -r requirements.txt pytest`.

```bash
python -m compileall -q app
python -m pytest -q
```

Для будущего Paper-запуска конфигурация находится в `.env.example`; `LIVE_ENABLED=false`.
Все обращения к биржам в основном процессе остаются публичными или read-only private reconciliation. Наличие API-ключей не разрешает отправку ордеров.

SQLite хранит дневник, Paper и durable LIVE-фазы. JSON RuntimeStore служит кешем. UNKNOWN и работающие ордера блокируют новый LIVE-допуск; закрытие считается подтверждённым только после private exposure=0.

Funding Paper рассчитывает направление на общем горизонте и фиксирует выход по свежим стаканам. Итог ожидает полную историческую проверку ставок; резерв остаётся занят до неё. Историческая ставка умножается на reference notional входа — это модель, а не фактическая выплата биржи.

Funding-прогноз не считается полученной прибылью. Статистика Paper и Replay — модельные результаты на REST-наблюдениях; реальные fills, latency и комиссии должны подтверждаться отдельно.

## История этапов

Предыдущие документы сохраняются как история реализации. Их формулировки «complete/ready» не являются текущим допуском к LIVE. Текущее состояние — в `RELEASE_STATUS_RU.md`.
