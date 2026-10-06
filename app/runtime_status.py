from dataclasses import dataclass
@dataclass(frozen=True)
class RuntimeStatus:
 mode:str;market_venues:int;private_venues:int;open_trades:int;open_notional:float;risk_halted:bool;live_enabled:bool;startup_reason:str
def build(scanner,runtime,risk,private_snapshot,startup,live_enabled):
 return RuntimeStatus(startup.mode,len(scanner.clients),len(private_snapshot),len(runtime.trades),runtime.open_notional,risk.state.halted,live_enabled,startup.reason)
def render(s):
 risk='HALT' if s.risk_halted else 'OK';live='ON' if s.live_enabled else 'OFF'
 return f'⚡ ARBITRAGE ENGINE\nРежим: {s.mode}\n🏦 Market: {s.market_venues} • Private: {s.private_venues}\n📈 Открыто: {s.open_trades} • ${s.open_notional:.2f}\n🛡 Risk: {risk}\n🔐 LIVE switch: {live}\n🧭 Startup: {s.startup_reason}'
