from .live_durable_session import Session
from .live_trade_store import Store


class LiveService(Session):
    """Validated executors are required; all live mutations use the durable session."""

    def __init__(self, store, journal, durable_store=None):
        if durable_store is None:
            if not hasattr(journal, "diary"):
                raise ValueError("DURABLE_STORE_REQUIRED")
            durable_store = Store(journal.diary.path)
        super().__init__(durable_store, store, journal)
