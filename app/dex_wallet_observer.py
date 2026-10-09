"""Read-only receipt monitor; no key loading, signing, approvals or broadcast."""

import json
from html import escape
import aiosqlite
from .dex_wallet import Journal, Reader


class Observer:
    def __init__(self, journal, primary, secondary):
        self.journal = journal
        self.reader = Reader(journal, primary, secondary)
        self.paper = self  # Observation continues while the scanner is paused.

    async def cycle(self):
        async with aiosqlite.connect(self.journal.path) as d:
            async with d.execute(
                "SELECT intent_id FROM wallet_tx_intents WHERE phase NOT IN ('FINALIZED_SUCCESS','FINALIZED_REVERT') ORDER BY created_at LIMIT 20"
            ) as c:
                ids = [r[0] for r in await c.fetchall()]
        rows = []
        for iid in ids:
            result = await self.reader.reconcile(iid)
            rows.append(
                dict(
                    strategy="wallet_receipts",
                    symbol=iid,
                    reason=result.get("reason", result["status"]),
                    status=result["status"],
                    live_allowed=False,
                )
            )
        return rows


async def render(path):
    journal = Journal(path)
    await journal.init()
    out = [
        "⛓ <b>DEX · транзакции кошелька</b>",
        "<i>Receipt сверяется двумя RPC: finalized block, точный hash/nonce/call, ERC20 transfer logs, token balances и фактически оплаченный native gas. UNKNOWN не повторяется автоматически.</i>",
    ]
    async with aiosqlite.connect(path) as d:
        async with d.execute(
            "SELECT intent_id,phase,tx_hash,payload FROM wallet_tx_intents ORDER BY created_at DESC LIMIT 10"
        ) as c:
            rows = await c.fetchall()
    for iid, phase, tx_hash, payload in rows:
        data = json.loads(payload)
        out.append(f"\n<b>{escape(iid)}</b> · {escape(phase)}")
        if tx_hash:
            out.append("Hash: <code>" + escape(tx_hash) + "</code>")
        if data.get("receipt"):
            r = data["receipt"]
            out.append(
                f"Raw sold / bought: {escape(r['sold_raw'])} / {escape(r['bought_raw'])}\nGas raw: {escape(r['gas_paid_raw'])}"
            )
    if not rows:
        out.append(
            "\nТранзакций нет. Backend не отправляет swaps из secondary scanner: автоматический CEX/DEX LIVE-координатор пока не подключён."
        )
    out.append(
        "\nТекущая политика backend: Ethereum mainnet, обычные ERC20, legacy gasPrice, заранее проверенный allowance. Другие сети/налоги/approval не допускаются."
    )
    return "\n".join(out)
