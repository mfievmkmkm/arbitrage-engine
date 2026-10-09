"""Persist actual live admission outcomes independently of Paper decisions."""


async def record(diary, strategy, op, result, clock):
    await diary.record_decisions(
        [
            dict(
                ts=clock(),
                strategy=strategy,
                symbol=op.get("symbol", op.get("future_symbol", "")),
                buy=op.get("buy", op.get("long_venue", op.get("exchange", ""))),
                sell=op.get("sell", op.get("short_venue", op.get("exchange", ""))),
                action="LIVE_" + str(result.get("status", "UNKNOWN")),
                reason=str(result.get("reason", result.get("status", "UNKNOWN"))),
                trade_id=result.get("trade_id", ""),
                live=True,
            )
        ]
    )
