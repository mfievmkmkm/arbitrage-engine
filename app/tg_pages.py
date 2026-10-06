def strategies(runtime):
    labels = {
        "futures_futures": "Futures ↔ Futures",
        "spot_futures": "Spot ↔ Futures",
        "spot_spot": "Spot ↔ Spot",
        "funding_arb": "Funding Arbitrage",
        "cex_dex": "CEX ↔ DEX",
    }
    out = ["<b>СТРАТЕГИИ</b>", "SCAN / PAPER / REAL", ""]
    for name, label in labels.items():
        enabled = getattr(runtime, "enabled", {}).get(name, False)
        mode = "PAPER" if name in ("futures_futures", "spot_futures") else "RESEARCH"
        out.append(
            f"<b>{label}</b>\n{'🟢 Включена' if enabled else '⚫ Выключена'} • {mode} • REAL 🔒\nНаблюдений сейчас: {runtime.counts().get(name,0)}"
        )
    out.append(
        "\nОтключение запрещает новые входы; открытые Paper-позиции продолжают наблюдаться."
    )
    return "\n\n".join(out)


def dex():
    return "<b>DEX LAB</b>\n<code>RESEARCH ENVIRONMENT</code>\n\nQuote engine      🟡 foundation\nRoute validation  🟢 ready\nToken policy      🟢 ready\nGas / impact      🟢 ready\nWallet execution  🔴 disabled\nLIVE              🔒 locked\n\n<i>Никаких on-chain транзакций до отдельного DEX acceptance.</i>"
