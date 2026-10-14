from dataclasses import dataclass
@dataclass(frozen=True)
class WalletBoundary:
 safe:bool;reason:str

def check(separate_wallet,limited_balance,no_seed_in_app):
 if not separate_wallet:return WalletBoundary(False,"DEDICATED_WALLET_REQUIRED")
 if not limited_balance:return WalletBoundary(False,"WALLET_BALANCE_NOT_LIMITED")
 if not no_seed_in_app:return WalletBoundary(False,"SEED_STORAGE_UNSAFE")
 return WalletBoundary(True,"OK")
