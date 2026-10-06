from dataclasses import dataclass
@dataclass(frozen=True)
class Preflight:
 ok:bool;errors:list;warnings:list
def check(config):
 errors=[];warnings=[]
 if not config.token:errors.append("BOT_TOKEN_MISSING")
 if not config.admin_id:errors.append("ADMIN_ID_MISSING")
 if config.notional<=0:errors.append("NOTIONAL_INVALID")
 if config.live_enabled:warnings.append("LIVE_SWITCH_ON_REQUIRES_PRIVATE_VALIDATION")
 if config.paper_capital<config.notional:warnings.append("PAPER_CAPITAL_BELOW_NOTIONAL")
 return Preflight(not errors,errors,warnings)
def render(x):
 rows=["✅ PREFLIGHT" if x.ok else "⛔ PREFLIGHT"]
 rows += ["ERROR: "+v for v in x.errors]+["WARN: "+v for v in x.warnings]
 return "\n".join(rows)
