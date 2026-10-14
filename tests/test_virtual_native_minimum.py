import asyncio
from decimal import Decimal

import ccxt

from app.cex_dex_paper import Engine
from app.cex_dex_paper_view import render


def test_real_ccxt_substep_is_explained_and_thirty_dollar_route_fits(tmp_path):
    from test_dex_virtual_paper import source

    async def go():
        s, route, public, provider, now = source("reference")
        route.update(notional_usd=20, asset_decimals=18)
        public.bid = 2550
        client = ccxt.bybit()
        client.markets = {route["cex_symbol"]: dict(symbol=route["cex_symbol"], base="X", quote="USDT", settle="USDT",
                            contract=True, linear=True, inverse=False, active=True, contractSize=1,
                            precision=dict(amount=.01, price=.01),
                            limits=dict(amount=dict(min=.01), cost=dict(min=5)), taker=.00055)}
        public.market = client.market
        public.amount_to_precision = client.amount_to_precision
        public.precisionMode = client.precisionMode

        async def price(chain, sell, buy, amount):
            raw = int(amount)
            got = int(Decimal(raw) / 10**6 / 2550 * 10**18) if sell == route["stable"] else int(Decimal(raw) / 10**18 * 2550 * 10**6)
            return dict(ok=True, chain_id=chain, sell_token=sell, buy_token=buy,
                        sell_amount_raw=amount, buy_amount_raw=str(got), ts=now[0])
        provider.price = price
        e = Engine(tmp_path / "native.db", s, clock=s.clock, virtual=True)
        await e.init()
        await e.cycle(route)
        assert e.state["last_decision"] == "DEX_VIRTUAL_AMOUNT_BELOW_NATIVE_STEP"
        assert not e.positions
        assert e.state["last_failure_detail"]["amount_step"] == .01
        assert "Шаг количества" in render(e)
        route["notional_usd"] = 30
        x = await s.entry(route)
        assert x["ok"], x
        assert x["position"]["cex_contracts"] == .01
        assert x["position"]["cex_contracts"] * x["position"]["entry_price"] <= 30
    asyncio.run(go())


def test_railway_default_eth_budget_uses_thirty_without_changing_saved_routes(tmp_path):
    import json
    from app.railway_paper_server import prepare_environment
    values = dict(BOT_TOKEN="placeholder", ADMIN_ID="1", ZEROX_API_KEY="placeholder",
                  DEX_VIRTUAL_PAPER_ENABLED="true", RAILWAY_VOLUME_MOUNT_PATH=str(tmp_path),
                  PAPER_SERVER_FRESH_START="true")
    data = prepare_environment(values, tmp_path)
    routes = json.loads(values["DEX_VIRTUAL_ROUTES_JSON"])
    assert routes[0]["notional_usd"] == 30
    routes[0]["notional_usd"] = 7
    (data / "watchlist.json").write_text(json.dumps(dict(routes=routes)))
    del values["DEX_VIRTUAL_ROUTES_JSON"]
    prepare_environment(values, tmp_path)
    assert json.loads(values["DEX_VIRTUAL_ROUTES_JSON"])[0]["notional_usd"] == 7
