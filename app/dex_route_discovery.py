"""Bounded Paper-only route discovery from explicit mints or verified metadata."""
import asyncio
import json
from pathlib import Path
from .build_virtual_watchlist import USDC, SEEDS, eligible_markets
from .dex_virtual_paper_source import routes_checked, route_key, token_key
from .dex_signal_focus import parse_signal
from .jupiter_research import mint_valid
from .dex_network_registry import stable_config

BYBIT_BASES = ("JTO", "RAY", "ORCA", "WIF", "BONK", "PYTH", "FARTCOIN", "POPCAT", "TRUMP", "PENGU")


class Discovery:
    def __init__(self, engines, path):
        self.engines, self.path = engines, Path(path)
        self.lock, self.task = asyncio.Lock(), None
        self.status = "Ожидает подбора рынков"

    def add(self, engine, route):
        if route_key(route) in {route_key(r) for r in engine.source.routes}:
            return
        if len(engine.source.routes) >= 24:
            raise ValueError("WATCHLIST_ROUTE_LIMIT_24")
        if any(r["chain_id"] == route["chain_id"] and r["base"] == route["base"] and token_key(r,r["asset"]) != token_key(route,route["asset"]) for r in engine.source.routes):
            raise ValueError("WATCHLIST_SYMBOL_IDENTITY_CONFLICT")
        routes_checked([route], 300)
        engine.source.routes.append(route)

    async def ensure(self, venue, base, mint):
        async with self.lock:
            engine = self.engines["dex_channel" if venue == "mexc" else "dex_bybit"]
            if any(r["chain_id"] == "solana" and r["base"] == base and r["asset"] == mint for r in engine.source.routes):
                return
            client = engine.source.public.get(venue)
            symbols = eligible_markets(client, base) if client else []
            if len(symbols) != 1:
                raise ValueError("Нет однозначного активного USDT-фьючерса для этого токена")
            if not mint_valid(mint):
                raise ValueError("SOLANA_MINT_INVALID")
            provider = engine.source.provider.solana
            token, stable = await provider.token(mint), await provider.token(USDC)
            if token.get("symbol", "").upper() not in (base, "WSOL" if base == "SOL" else base) or stable.get("symbol") != "USDC" or stable.get("decimals") != 6:
                raise ValueError("Тикер токена по точному адресу не совпадает с рынком биржи")
            route = dict(chain_id="solana", stable=USDC, stable_symbol="USDC", asset=mint,
                         stable_decimals=6, asset_decimals=token["decimals"], base=base,
                         cex_venue=venue, cex_symbol=symbols[0], native_symbol="SOL/USDT:USDT",
                         native_decimals=9, mode="reference", notional_usd=engine.state["experiment_config"].get("notional", 100), max_notional_usd=engine.state["experiment_config"].get("notional", 100),
                         label=f"{base} · {venue}", identity_source="PUBLIC_METADATA_PAPER_ONLY")
            self.add(engine, route)
            self.save()

    async def signal(self, text, timestamp):
        s = parse_signal(text)
        engine = self.engines["dex_channel"]
        if not 0 <= engine.clock() - timestamp <= 90:
            raise ValueError("Сигнал старше 90 секунд")
        if s["chain_id"] != "solana":
            await self.ensure_evm("mexc", s)
            try:
                await self.ensure_evm("bybit", s)
            except ValueError:
                pass
            return
        await self.ensure("mexc", s["base"], s["asset"])
        # Explicit identity may also extend the independent Bybit scan, if listed.
        try:
            await self.ensure("bybit", s["base"], s["asset"])
        except ValueError:
            pass

    async def ensure_evm(self, venue, signal):
        async with self.lock:
            engine = self.engines["dex_channel" if venue == "mexc" else "dex_bybit"]
            chain, asset, base = signal["chain_id"], signal["asset"], signal["base"]
            if any(r["chain_id"]==chain and r["asset"].lower()==asset.lower() and r["base"]==base for r in engine.source.routes):
                return
            client = engine.source.public.get(venue)
            symbols = eligible_markets(client, base) if client else []
            if len(symbols)!=1:
                raise ValueError("Нет однозначного активного USDT-фьючерса для этого токена")
            network, stable_address = stable_config(chain)
            provider = engine.source.provider.evm
            if not provider.api_key:
                raise ValueError("Для EVM-котировок нужен ZEROX_API_KEY")
            token = await provider.token(network, asset)
            stable = await provider.token(network, stable_address)
            if token['symbol'].upper()!=base or stable['symbol'].upper() not in ('USDC','USDT'):
                raise ValueError("Тикер по точному EVM-контракту не совпадает с рынком")
            amount=engine.state['experiment_config']['notional']
            route=dict(chain_id=chain,stable=stable_address,stable_symbol=stable['symbol'],
                       asset=asset,stable_decimals=stable['decimals'],asset_decimals=token['decimals'],
                       base=base,cex_venue=venue,cex_symbol=symbols[0],native_symbol='ETH/USDT:USDT',
                       native_decimals=18,mode='reference',notional_usd=amount,max_notional_usd=amount,
                       label=f'{base} · {venue} · {network}',identity_source='EXPLICIT_EVM_CONTRACT_PUBLIC_METADATA_PAPER_ONLY')
            self.add(engine,route)
            self.save()

    def save(self):
        content = {name: e.source.routes for name, e in self.engines.items()}
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(content), encoding="utf-8")
        temp.replace(self.path)

    async def start(self):
        if self.path.exists():
            cached = json.loads(self.path.read_text())
            for name, rows in cached.items():
                if name not in self.engines:
                    continue
                for r in routes_checked(rows, 300):
                    e = self.engines[name]
                    venue = e.state["experiment_config"]["venue"]
                    if r["cex_venue"] != venue or r.get("mode") != "reference":
                        raise ValueError("WATCHLIST_CACHE_SCOPE_INVALID")
                    self.add(e, dict(r, notional_usd=e.state["experiment_config"]["notional"], max_notional_usd=e.state["experiment_config"]["notional"]))
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self):
        provider = self.engines["dex_bybit"].source.provider.solana
        while True:
            try:
                for base, mint in SEEDS.items():
                    for venue in ("mexc", "bybit"):
                        try:
                            await self.ensure(venue, base, mint)
                        except ValueError:
                            pass
                for base in BYBIT_BASES:
                    client = self.engines["dex_bybit"].source.public.get("bybit")
                    if not client or not eligible_markets(client, base):
                        continue
                    if any(r["base"] == base for r in self.engines["dex_bybit"].source.routes):
                        continue
                    data = await provider.get("tokens/v2/search", {"query": base})
                    matches = [t for t in data if isinstance(t, dict) and t.get("symbol", "").upper() == base
                               and t.get("isVerified") is True and isinstance(t.get("liquidity"), (float, int)) and t["liquidity"] >= 100000] if isinstance(data, list) else []
                    if len(matches) == 1:
                        await self.ensure("bybit", base, matches[0]["id"])
                self.status = "Рынки обновлены · до 24 на режим"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status = "Подбор временно недоступен: " + type(exc).__name__
            await asyncio.sleep(600)
