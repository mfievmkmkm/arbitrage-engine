from html import escape


def render(ledger, allocations=None):
    out = [
        "💼 <b>Капитал · Paper</b>",
        f"\nБаланс модели: <b>{ledger.equity:.2f} USD</b>",
        f"Старт: {ledger.starting:.2f} USD",
        f"Закрытый NET: {ledger.realized:+.4f} USD",
    ]
    if allocations:
        out.append("Распределение: " + escape(str(allocations)))
    out.append(
        "\n<i>NET уже включает модельные затраты. Это учебный баланс, не баланс биржевого аккаунта.</i>"
    )
    return "\n".join(out)
