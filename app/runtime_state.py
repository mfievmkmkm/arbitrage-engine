from dataclasses import dataclass, asdict


@dataclass
class RuntimeTrade:
    trade_id: str
    symbol: str
    long_venue: str
    short_venue: str
    base_qty: float
    long_contracts: float
    short_contracts: float
    long_contract_size: float
    short_contract_size: float
    long_entry: float
    short_entry: float
    opened_at: float
    phase: str = "HEDGED"
    entry_fees: float = 0.0
    funding: float = 0.0
    recovery_gross: float = 0.0
    recovery_fees: float = 0.0
    recovery_capital: float = 0.0

    def row(self):
        return asdict(self)
