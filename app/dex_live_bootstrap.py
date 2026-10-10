"""Explicit configured composition; never loaded by read-only wallet observation."""

import json
import os
from pathlib import Path
from .db import Diary
from .dex_wallet import Policy, Sender, Signer, Reader
from .dex_cex_backend import Backend
from .dex_live_bridge import Session, dec
from .dex_live_costs import Reader as Costs
from .dex_live_admission import Admission
from .dex_live_runtime import Source, Marker, Coordinator
from .dex_firm_simulation import Provider, address, integer
from .live_trade_store import Store


def enabled(env):
    value = env.get("DEX_LIVE_ENABLED", "false").strip().lower()
    if value not in ("true", "false", "1", "0"):
        raise ValueError("DEX_LIVE_FLAG_INVALID")
    return value in ("true", "1")


async def build(
    path,
    wallet,
    primary,
    secondary,
    public_clients,
    private_clients,
    funding_service,
    options,
    env=None,
):
    env = os.environ if env is None else env
    # No policy/key/API/RPC configuration is inspected unless both explicit flags are set.
    if not enabled(env) or not options or options.get("live_enabled") is not True:
        return None
    if primary is None or secondary is None or funding_service is None:
        raise ValueError("DEX_LIVE_DUAL_RPC_AND_FUNDING_REQUIRED")
    raw = json.loads(
        Path(env.get("DEX_WALLET_POLICY_PATH", "dex_wallet_policy.json")).read_text()
    )
    if (
        set(raw)
        != {
            "version",
            "wallet",
            "cex_venue",
            "acceptance_path",
            "targets",
            "max_sell_raw",
            "max_gas_raw",
        }
        or raw["version"] != 1
        or type(raw["version"]) is not int
    ):
        raise ValueError("DEX_WALLET_POLICY_CONFIG_INVALID")
    venue = raw["cex_venue"]
    if venue not in public_clients or venue not in private_clients:
        raise ValueError("DEX_LIVE_CLIENT_SCOPE_REQUIRED")
    if (
        not isinstance(raw["targets"], list)
        or not raw["targets"]
        or not isinstance(raw["max_sell_raw"], dict)
        or not raw["max_sell_raw"]
    ):
        raise ValueError("DEX_WALLET_POLICY_CAPS_REQUIRED")
    policy = Policy(
        address(raw["wallet"]),
        venue,
        raw["acceptance_path"],
        tuple(map(address, raw["targets"])),
        {
            address(k): integer(v, "WALLET_SELL_CAP")
            for k, v in raw["max_sell_raw"].items()
        },
        integer(raw["max_gas_raw"], "WALLET_GAS_CAP"),
        True,
    )
    # Error messages never include the configured key, raw tx or RPC URL.
    try:
        signer = Signer(env.get("DEX_WALLET_PRIVATE_KEY", ""), policy.wallet)
    except Exception:
        raise ValueError("DEX_ISOLATED_SIGNER_CONFIG_INVALID") from None
    entry, exit_gate = options["entry_authority"], options["exit_authority"]
    provider = Provider(env.get("ZEROX_API_KEY"), primary.url)
    try:
        await wallet.init()
        diary, store = Diary(path), Store(path)
        await diary.init()
        await store.init()
        backend = Backend(store, diary, private_clients, entry, exit_gate)
        registry_path = env.get("DEX_TOKEN_REGISTRY_PATH", "dex_token_registry.json")
        registry = lambda: json.loads(Path(registry_path).read_text())
        routes = json.loads(env.get("DEX_LIVE_ROUTES_JSON", "[]"))
        source = Source(
            provider,
            registry,
            policy,
            private_clients,
            routes,
            None,
            budget=options.get("budget", 5),
            safety=options.get("safety", "0.005"),
            entry_gate=entry,
        )
        admission = Admission(
            path,
            backend,
            policy,
            registry_path,
            options["acceptance_path"],
            public_clients[venue],
            funding_service,
            source.reverse,
            bankroll=options.get("bankroll", 50),
            minimum_net=options.get("minimum_net", "0.05"),
            hold_seconds=min(3600, options.get("max_seconds", 1200)),
        )
        source.admission = admission
        sender = Sender(
            wallet,
            primary,
            secondary,
            signer,
            policy,
            lambda tid, closing: exit_gate(venue) if closing else entry(venue),
        )
        session = Session(
            path,
            sender,
            Reader(wallet, primary, secondary),
            backend,
            admission,
            source.reverse,
            Costs(private_clients, public_clients[venue]),
            entry,
            exit_gate,
            hedge_admission=admission.hedge,
        )
        await session.init()
        marker = Marker(
            session,
            admission,
            funding_service,
            max_seconds=options.get("max_seconds", 1200),
            target=options.get("target", 0.7),
            trailing=options.get("trailing", 0.2),
            stop_net=-float(dec(options.get("bankroll", 50))) * 0.01,
            pending_seconds=env.get("DEX_MAX_UNHEDGED_SECONDS", "900"),
        )
        coordinator = Coordinator(
            session, source, marker, env.get("DEX_MAX_UNHEDGED_SECONDS", "900")
        )
        coordinator.provider = provider
        return coordinator
    except BaseException:
        await provider.close()
        raise
