"""Local setup for virtual DEX Paper. Preserves credentials and database.

No network requests. Use --routes-file for an explicit EVM watchlist.
"""

import argparse
import json
import re
import shutil
import time
from pathlib import Path

from .dex_virtual_paper_source import routes_checked


def default_routes(mode, notional=None):
    route = dict(chain_id=1, stable="0xdac17f958d2ee523a2206206994597c13d831ec7",
                 asset="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", stable_decimals=6,
                 asset_decimals=18, base="ETH", cex_venue="bybit", cex_symbol="ETH/USDT:USDT",
                 native_symbol="ETH/USDT:USDT", native_decimals=18, notional_usd=notional if notional is not None else (30 if mode == "reference" else 5),
                 label="Ethereum WETH / Bybit virtual", mode=mode)
    return [route]


def configure(env_path, routes):
    routes_checked(routes)
    path = Path(env_path)
    if not path.is_file():
        raise ValueError("ENV_FILE_MISSING")
    backup = path.with_name(path.name + ".virtual-backup-" + str(time.time_ns()))
    shutil.copy2(path, backup)
    values = {"LIVE_ENABLED": "false", "LIVE_ENTRY_ENABLED": "false", "DEX_LIVE_ENABLED": "false",
              "DEX_VIRTUAL_PAPER_ENABLED": "true",
              "DEX_VIRTUAL_ROUTES_JSON": json.dumps(routes, separators=(",", ":"), ensure_ascii=False)}
    # Leave unrelated settings, secrets, Paper capital and history untouched.
    rows = path.read_text(encoding="utf-8-sig").splitlines()
    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=")
    result = []
    for row in rows:
        match = pattern.match(row)
        if not match or match.group(1) not in values:
            result.append(row)
    result.extend(key + "=" + value for key, value in values.items())
    path.write_text("\n".join(result) + "\n", encoding="utf-8")
    return backup


def main():
    parser = argparse.ArgumentParser(description="Configure virtual DEX Paper without wallet funds")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--mode", choices=("paired", "reference"), default="reference")
    parser.add_argument("--routes-file")
    parser.add_argument("--notional", type=float)
    args = parser.parse_args()
    routes = json.loads(Path(args.routes_file).read_text(encoding="utf-8-sig")) if args.routes_file else default_routes(args.mode, args.notional)
    backup = configure(args.env, routes)
    print("OK: virtual DEX Paper enabled; LIVE disabled")
    print("Routes:", len(routes))
    print("Backup:", backup.name)
    print("Wallet funds, approvals and private key are not required.")
    print("Default route is WETH/Bybit only; no Telegram or Solana discovery.")


if __name__ == "__main__":
    main()
