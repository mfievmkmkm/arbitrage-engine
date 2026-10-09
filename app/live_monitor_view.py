import time
from html import escape
from datetime import datetime, timezone

REASONS = {
    "UNRESOLVED_ORDER": "Биржа ещё не подтвердила итог заявки. Повторная отправка запрещена.",
    "PRIVATE_SNAPSHOT_UNTRUSTED": "Нет свежих доверенных данных о позициях.",
    "HEDGE_FILL_MISMATCH": "Объёмы исполнений двух ног не совпадают.",
    "PERSISTED_PRIVATE_MISMATCH": "Позиции биржи не совпадают с сохранённой сделкой.",
    "EXIT_RESIDUAL_VERIFIED": "Остаток совпал по terminal fills и приватным контрактам. Закрытие требует действующего write-допуска; итог ещё не подтверждён.",
    "EXIT_RESIDUAL_REQUIRES_RECOVERY": "После попытки выхода осталась экспозиция. Нужен контролируемый recovery.",
    "CLOSE_ACCOUNTING_PENDING": "Позиции закрыты, но комиссии или funding ещё не подтверждены.",
    "UNMANAGED_POSITION": "Обнаружена позиция, которой нет в журнале бота.",
    "UNMANAGED_WORKING_ORDER": "Обнаружена работающая заявка вне журнала бота.",
    "EXIT_MARKET_UNVERIFIED": "Нет свежего стакана или подтверждённых данных для оценки выхода.",
    "ORDER_EVIDENCE_MISSING": "Нет ордерных доказательств происхождения позиции.",
    "ENTRY_REDUCTION_REQUIRES_PRIVATE_ACCOUNTING": "Лишний объём сокращён по fills. Остаток и итоговый учёт требуют private-сверки; новый вход заблокирован.",
    "ENTRY_WITH_CLOSE_FILLS_REQUIRES_ACCOUNTING": "В незавершённом входе есть закрывающие fills. Учёт расходов и остатка нельзя восстанавливать как обычный вход.",
    "REDUCED_RUNTIME_ACCOUNTING_MISMATCH": "Сохранённый остаток или расходы не совпадают с ордерной историей. Нужна сверка; запись не используется как подтверждение исполнения.",
    "REDUCED_EVIDENCE_MISSING": "В сохранённом результате есть аварийное сокращение, но подтверждающих ордерных данных нет.",
    "ZERO_FILL_ACCOUNTING_REQUIRED": "Исполненного объёма нет, но расходы неизвестны или ненулевые. Сделку нельзя списать как бесплатную отмену.",
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
        "Наблюдатель сверяет данные. Допуск отправки заявок указан в LIVE-контроле."
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
        if x.get("exit_signal") == "EXIT_RESIDUAL":
            out.append(
                f"Подтверждённый остаток: LONG {x['residual_long_contracts']:.8g} / SHORT {x['residual_short_contracts']:.8g} контрактов."
            )
        if x.get("private_flat"):
            out.append("Экспозиция: ноль; ожидается окончательный учёт.")
        effects = x.get("recovery_effects")
        if effects:
            out.append(
                f"Сокращено по fills: {effects['reduced_base']:.8g} BASE\nРезультат сокращения без funding: {effects['net_excluding_funding']:+.4f} USD\nОстаток по fills: LONG {effects['remaining_long_base']:.8g} / SHORT {effects['remaining_short_base']:.8g} BASE\nИтог сделки ещё не подтверждён."
            )
        assessment = x.get("recovery_assessment")
        if assessment:
            out.append(
                "Сценарий восстановления: <code>"
                + escape(str(assessment["action"]))
                + "</code> • модель, без разрешения на ордер"
            )
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
