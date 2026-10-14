from .live_session import open_trade, close_trade


class Session:
    def __init__(self, durable_store, runtime_store, journal):
        self.durable = durable_store
        self.runtime = runtime_store
        self.journal = journal

    async def open(self, existing, entry_args, commit_args):
        return await open_trade(
            self.runtime,
            self.journal,
            existing,
            entry_args,
            commit_args,
            durable=self.durable,
        )

    async def close(
        self,
        trades,
        trade,
        long_executor,
        short_executor,
        private_snapshot,
        reason="EXIT",
        recovery_market_reader=None,
    ):
        return await close_trade(
            self.runtime,
            self.journal,
            trades,
            trade,
            long_executor,
            short_executor,
            private_snapshot,
            reason,
            durable=self.durable,
            recovery_market_reader=recovery_market_reader,
        )
