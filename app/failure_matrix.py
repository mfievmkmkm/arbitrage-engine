from dataclasses import dataclass
@dataclass(frozen=True)
class FailureCase:
 name:str
 entry_allowed:bool
 expected:str
def cases():
 return (
  FailureCase("stale_books",False,"MARKET_DATA_STALE"),
  FailureCase("unknown_funding",False,"FUNDING_UNKNOWN"),
  FailureCase("unverified_fee",False,"FEE_UNVERIFIED"),
  FailureCase("unknown_order",False,"UNKNOWN_ORDERS"),
  FailureCase("private_untrusted",False,"PRIVATE_UNVERIFIED"),
  FailureCase("withdraw_permission",False,"WITHDRAW_PERMISSION"),
  FailureCase("venue_missing_client_id",False,"VENUE_CAPABILITY"),
  FailureCase("daily_stop",False,"DAILY_STOP"),
  FailureCase("max_open_trade",False,"CAPACITY"),
  FailureCase("kill_global",False,"KILL_GLOBAL"),
  FailureCase("kill_venue",False,"KILL_VENUE"),
  FailureCase("kill_pair",False,"KILL_PAIR"),
  FailureCase("partial_fill",False,"PROTECTIVE_FLATTEN"),
  FailureCase("actual_net_deteriorated",False,"FLATTEN"),
  FailureCase("private_close_residual",False,"CLOSE_UNVERIFIED"),
  FailureCase("restart_size_mismatch",False,"PERSISTED_PRIVATE_MISMATCH"),
  FailureCase("flipped_position",False,"FLIPPED"),
  FailureCase("api_error_streak",False,"ERROR_CIRCUIT"),
 )
