import asyncio
from types import SimpleNamespace

import pytest

from app.market_console import render
from app.spot_future_cycle_service import CycleService
from app.spot_future_paper_engine import Engine
from app.strategy_paper_coordinator import Coordinator


def quote(plan=None):
    op = dict(
        base="OPENAI",
        exchange="fixture",
        direction="LONG_SPOT_SHORT_FUTURE",
        hypothetical_edge=74,
        notional=5,
        base_qty=0.05,
        prices=dict(spot_buy=100, spot_sell=99.9, future_buy=174.1, future_sell=174),
        fee_pct=0.2,
        safety_pct=0.1,
        funding_pct=0,
        ts=1000,
    )
    if plan is not None:
        op["native_plan"] = plan
    return op


@pytest.mark.parametrize(
    "plan",
    [dict(valid=False, reason="NATIVE_AMOUNT_FORMAT_FAILED"), {}, dict(valid="true")],
)
def test_invalid_native_plan_never_opens_even_with_large_edge(plan):
    engine = Engine()
    assert engine.open(quote(plan)) is None
    assert not engine.positions


@pytest.mark.parametrize("plan", [None, {}, dict(valid=False), dict(valid="true")])
def test_runtime_requires_explicit_valid_plan_but_keeps_research_visible(plan):
    async def go():
        op = quote(plan)

        class Service:
            source = SimpleNamespace()

            async def cycle(self):
                return [op], []

        engine = Engine()
        cycle = CycleService(Service(), Coordinator(engine), entry_edge=2)
        assert await cycle.cycle() == [op]
        assert not engine.positions

    asyncio.run(go())


def test_existing_position_still_updates_and_closes_with_blocked_new_entry_plan():
    async def go():
        op = quote(dict(valid=True))

        class Service:
            source = SimpleNamespace()

            async def cycle(self):
                return [op], []

        engine = Engine(max_age=600)
        cycle = CycleService(Service(), Coordinator(engine), entry_edge=2)
        await cycle.cycle()
        assert len(engine.positions) == 1
        op["ts"] = 1601
        op["native_plan"] = dict(valid=False, reason="NATIVE_AMOUNT_FORMAT_FAILED")
        await cycle.cycle()
        assert not engine.positions
        assert len(engine.closed) == 1 and engine.closed[0].status == "TIME_STOP"

    asyncio.run(go())


def test_market_distinguishes_blocked_potential_from_realized_net():
    op = quote(dict(valid=False, reason="<NATIVE_AMOUNT_FORMAT_FAILED>"))
    text = render(SimpleNamespace(latest={"spot_futures": [op]}))
    assert "Потенциал схождения" in text
    assert "План заявок заблокирован" in text
    assert "&lt;NATIVE_AMOUNT_FORMAT_FAILED&gt;" in text
    assert "<NATIVE_AMOUNT_FORMAT_FAILED>" not in text
