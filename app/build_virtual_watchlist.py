"""Read-only watchlist builder. Exact mint seeds, native CEX markets, no orders.

Adds to the current route list rather than abandoning persisted positions.
Channel seeds are user-supplied identities, not LIVE-certified tokens.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import dotenv_values

from .configure_virtual_paper import configure, default_routes
from .dex_virtual_paper_source import route_key, routes_checked
from .jupiter_research import Provider
from .public_client_factory import build, close

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SEEDS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "GP": "HTmQz7My6MehV7bjhJ6jde8nDND1yvsz68d24LP7YgUQ",
    "OTC": "MukLDtJ8Cx9DxLbeyLRSWPSposTMWuwHANbuaudpump",
    "BATON": "Hg5Ja55T5wESq4vyFoiVCMeHXtGyVA69X2UHq8hgpump",
    "MASKSOL": "HuAXPyDWDaMYFKuwQHpqL1oPnj93zdzWmtvFGzCeCUa7",
}


def eligible_markets(client, base):
    return sorted(m["symbol"] for m in client.markets.values()
                  if m.get("base") == base and m.get("quote") == "USDT" and m.get("settle") == "USDT"
                  and m.get("swap") is True and m.get("linear") is True and m.get("active") is True
                  and isinstance(m.get("contractSize"), (int, float)) and m["contractSize"] > 0)


async def generate(existing, clients, provider, seeds, notional=20):
    routes = [dict(r) for r in routes_checked(existing)]
    keys, report = {route_key(r) for r in routes}, []
    stable = await provider.token(USDC)
    if stable["decimals"] != 6 or stable.get("symbol") != "USDC":
        raise ValueError("SOLANA_STABLE_METADATA_MISMATCH")
    for base, mint in seeds.items():
        candidates = [(venue, symbol) for venue, c in clients.items() for symbol in eligible_markets(c, base)]
        if not candidates:
            report.append(dict(base=base, status="SKIPPED", reason="CEX_LINEAR_USDT_MARKET_MISSING"))
            continue
        try:
            token = await provider.token(mint)
            if token.get("symbol") not in (base, "WSOL" if base == "SOL" else base):
                raise ValueError("CONFIGURED_MINT_SYMBOL_MISMATCH")
            q = await provider.price("solana", USDC, mint, str(int(notional * 10**6)))
            if not q.get("ok"):
                raise ValueError(q["reason"])
            reverse = await provider.price("solana", mint, USDC, q["min_buy_amount_raw"])
            if not reverse.get("ok"):
                raise ValueError(reverse["reason"])
            for venue, symbol in candidates:
                r = dict(chain_id="solana", stable=USDC, stable_symbol="USDC", asset=mint,
                         stable_decimals=6, asset_decimals=token["decimals"], base=base,
                         cex_venue=venue, cex_symbol=symbol, native_symbol="SOL/USDT:USDT",
                         native_decimals=9, mode="reference", notional_usd=notional,
                         label=f"Solana {base} / {venue} virtual", identity_source="EXPLICIT_MINT_SEED")
                if route_key(r) not in keys:
                    routes.append(r)
                    keys.add(route_key(r))
                report.append(dict(base=base, venue=venue, symbol=symbol, status="CONFIGURED",
                                   execution_verified=False, live_allowed=False))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            report.append(dict(base=base, status="SKIPPED", reason=str(exc) if isinstance(exc, ValueError) else "METADATA_OR_QUOTE_UNAVAILABLE"))
    return routes_checked(routes), report


async def run(args):
    env = dotenv_values(args.env)
    key = os.getenv("JUPITER_API_KEY") or env.get("JUPITER_API_KEY")
    if not key:
        raise ValueError("JUPITER_API_KEY_MISSING: add it to .env locally")
    venues = list(dict.fromkeys(x.strip() for x in args.venues.split(",") if x.strip()))
    configured = {x.strip() for x in (env.get("EXCHANGES") or "binance,bybit,okx,bitget,gateio,mexc,bingx").split(",")}
    if not venues or len(venues) > 7 or not set(venues) <= configured:
        raise ValueError("WATCHLIST_VENUE_NOT_IN_EXCHANGES")
    clients, provider = await build(venues, "swap"), Provider(key)
    try:
        if not clients:
            raise ValueError("WATCHLIST_CEX_CONNECTIONS_UNAVAILABLE")
        seeds = json.loads(Path(args.seeds_file).read_text(encoding="utf-8-sig")) if args.seeds_file else (SEEDS if args.preset == "channel" else {k: SEEDS[k] for k in ("SOL", "JUP")})
        if not isinstance(seeds, dict) or not 0 < len(seeds) <= 20:
            raise ValueError("WATCHLIST_SEEDS_INVALID")
        existing = json.loads(env.get("DEX_VIRTUAL_ROUTES_JSON") or "[]") or default_routes("reference")
        routes, report = await generate(existing, clients, provider, seeds, args.notional)
        output = Path(args.output)
        if output.exists():
            raise ValueError("WATCHLIST_REPORT_EXISTS: choose another --output")
        with output.open("x", encoding="utf-8") as stream:
            json.dump(dict(routes=routes, report=report, live_allowed=False), stream, ensure_ascii=False, indent=2)
        if args.apply:
            configure(args.env, routes)
        for row in report:
            print(row["base"], row["status"], row.get("venue", ""), row.get("reason", row.get("symbol", "")))
        print("Total routes:", len(routes), "| Saved:", output.name)
        print("Applied to .env; restart bot." if args.apply else "Preview only. Use --apply to extend .env.")
    finally:
        await close(clients)
        await provider.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--venues", default="bybit")
    parser.add_argument("--preset", choices=("core", "channel"), default="channel")
    parser.add_argument("--seeds-file")
    parser.add_argument("--notional", type=float, default=20)
    parser.add_argument("--output", default="virtual_watchlist_report.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.notional <= 30:
        parser.error("--notional must be between 1 and 30")
    try:
        asyncio.run(run(args))
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
