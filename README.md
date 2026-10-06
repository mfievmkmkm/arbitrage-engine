# Arbitrage Engine

Русскоязычный Telegram-интерфейс исследования межбиржевого арбитража.
Рабочая версия находится в **`phase-2-discovery`**; `main` содержит старый Discovery MVP.

Текущий этап: интегрированный Discovery/Paper/Replay. **Автоматическая реальная торговля и AUTO ещё не выпущены.** Фактические возможности и незавершённые пункты описаны в [RELEASE_STATUS_RU.md](RELEASE_STATUS_RU.md).

## Работающий запуск

`app.main` запускает Futures/Futures scanner и Paper, Spot/Futures scanner и сохраняемый Paper, исследовательские Spot/Spot и Funding loops. Для DEX доступен опциональный 0x price-research; кошелёк не подписывает и не отправляет транзакции.

Telegram: `/start`, `/top`, `/paper`, `/strategies`, `/exchanges`, `/diary`, `/replay`, `/capital`, `/risk`, `/startup`, `/live`, `/live_stop`, `/live_resume`, `/export`, `/pause`, `/resume`.

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

Funding-прогноз не считается полученной прибылью. Статистика Paper и Replay — модельные результаты на REST-наблюдениях; реальные fills, latency и комиссии должны подтверждаться отдельно.

## История этапов

Предыдущие документы сохраняются как история реализации. Их формулировки «complete/ready» не являются текущим допуском к LIVE. Текущее состояние — в `RELEASE_STATUS_RU.md`.
