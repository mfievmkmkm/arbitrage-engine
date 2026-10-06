import time
from html import escape
from datetime import datetime, timezone

REASONS = {
    "UNRESOLVED_ORDER": "Биржа ещё не подтвердила итог заявки. Повторная отправка запрещена.",
    "PRIVATE_SNAPSHOT_UNTRUSTED": "Нет свежих доверенных данных о позициях.",
    "HEDGE_FILL_MISMATCH": "Объёмы исполнений двух ног не совпадают.",
    "PERSISTED_PRIVATE_MISMATCH": "Позиции биржи не совпадают с сохранённой сделкой.",
    "EXIT_RESIDUAL_REQUIRES_RECOVERY": "После попытки выхода осталась экспозиция. Нужен контролируемый recovery.",
    "CLOSE_ACCOUNTING_PENDING": "Позиции закрыты, но комиссии или funding ещё не подтверждены.",
    "UNMANAGED_POSITION": "Обнаружена позиция, которой нет в журнале бота.",
    "UNMANAGED_WORKING_ORDER": "Обнаружена работающая заявка вне журнала бота.",
    "EXIT_MARKET_UNVERIFIED": "Нет свежего стакана или подтверждённых данных для оценки выхода.",
    "ORDER_EVIDENCE_MISSING": "Нет ордерных доказательств происхождения позиции.",
}


def status(summary):
    if not summary:
        return "🔐 Непрерывная сверка ещё не выполнена."
    stamp = datetime.fromtimestamp(summary["ts"], timezone.utc).strftime("%H:%M:%S UTC")
    fresh = 0 <= time.time() - summary["ts"] <= 30
    net = summary.get("realized", {}).get("net", 0)
    return (
        f"🔎 <b>Непрерывная сверка</b> • {stamp}\n"
        f"Private: {'подтверждён' if summary['private_verified'] and fresh else 'не подтверждён'}\n"
        f"Восстановление: {'сверено' if summary['reconciled'] and fresh else 'требует проверки'}\n"
        f"Неизвестных заявок: {summary['unknown_orders']}\nНезавершённых сделок в БД: {summary['active_db']}\n"
        f"Подтверждённый NET закрытых сделок: {net:+.4f} USD\n"
        "Режим: чтение и наблюдение. Отправка ордеров заблокирована."
    )


def positions(summary):
    out = ["📈 <b>LIVE-состояние из БД и private API</b>"]
    if not summary or not summary["trades"]:
        return "\n".join(out + ["Незавершённых LIVE-сделок нет."])
    for x in summary["trades"]:
        out.append(
            f"\n<b>{escape(str(x.get('symbol','—')))}</b>\n<code>{escape(x['trade_id'])}</code>\n"
            f"{escape(str(x['long_venue']))} LONG / {escape(str(x['short_venue']))} SHORT\n"
            f"Этап: <code>{escape(x['phase'])}</code>\nPrivate: {'сверено' if x.get('private_verified') else 'не подтверждено'}"
        )
        if x.get("private_flat"):
            out.append("Экспозиция: ноль; ожидается окончательный учёт.")
        if x.get("estimated_net") is not None:
            out.append(
                f"Оценка выхода: {x['estimated_net']:+.4f} USD\nFunding: {'подтверждён' if x.get('funding_known') else 'не полностью подтверждён'}"
            )
        if x.get("exit_signal"):
            out.append("Сигнал выхода: <code>" + escape(x["exit_signal"]) + "</code>")
    return "\n".join(out)


def incidents(summary):
    out = ["🛡 <b>Инциденты исполнения</b>"]
    if not summary or not summary["incidents"]:
        return "\n".join(out + ["Активных инцидентов нет."])
    for x in summary["incidents"][:15]:
        out.append(
            f"\n{'🔴' if x['severity'] in ('CRITICAL','HIGH') else '🟡'} <code>{escape(x['code'])}</code>\n"
            f"Сделка: <code>{escape(x.get('trade_id') or 'система')}</code>\n"
            + REASONS.get(x["code"], "Подробности сохранены в дневнике.")
        )
    return "\n".join(out)
