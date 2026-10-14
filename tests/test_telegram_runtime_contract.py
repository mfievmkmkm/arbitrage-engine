import asyncio
from types import SimpleNamespace as NS
from app.tg_ui import main_menu, live_menu, strategy_menu, replay_menu
from app.tg_risk_center import render as risk_view
from app.tg_incident_banner import render as banner
from app.tg_market_detail import render as detail
from app.tg_market_keyboard import build
from app.risk import RiskGuard
from app.live_supervisor import LiveSupervisor
from app.persistent_stop import Stop
from app.strategy_runtime import StrategyRuntime
from app.market_console import merged, render as market


def test_native_fill_styles_and_callbacks_serialize():
    menu = main_menu().model_dump(exclude_none=True)["inline_keyboard"]
    buttons = {x["callback_data"]: x for row in menu for x in row}
    assert buttons["live_stop"]["style"] == "danger"
    assert buttons["top"]["style"] == "primary"
    assert "resume" not in buttons and "pause" in buttons
    paused = {
        b.callback_data: b for row in main_menu(True).inline_keyboard for b in row
    }
    assert paused["resume"].style == "success" and "pause" not in paused
    for markup in [
        main_menu(),
        live_menu(True),
        strategy_menu(StrategyRuntime().enabled),
        replay_menu("sf_replay"),
    ]:
        for row in markup.inline_keyboard:
            for b in row:
                assert 1 <= len(b.callback_data.encode()) <= 64


def test_actual_supervisor_bool_and_risk_state_render_without_type_errors(tmp_path):
    supervisor = LiveSupervisor()
    stop = Stop(tmp_path / "stop.json")
    risk = RiskGuard()
    assert "есть · требуется сверка" in risk_view(risk, supervisor, stop)
    assert "Неизвестные заявки" in banner(supervisor, stop)
    supervisor.unknown_orders = False
    assert "Неизвестные заявки: <b>нет</b>" in risk_view(risk, supervisor, stop)
    assert "Неизвестные заявки" not in banner(supervisor, stop)


def test_exchange_supplied_labels_are_escaped_and_nonfinite_edge_not_ranked():
    runtime = StrategyRuntime()
    runtime.latest = {
        "spot_futures": [dict(base="<X>", exchange="A&B", net=2)],
        "funding_arb": [dict(base="Y", net=float("nan"))],
    }
    assert len(merged(runtime)) == 1
    assert "&lt;X&gt;" in market(runtime) and "A&amp;B" in market(runtime)
    x = detail("spot_futures", dict(base="<X>", exchange="A&B", hypothetical_edge=2))
    assert "&lt;X&gt;" in x and "A&amp;B" in x


def test_replay_and_strategy_navigation_are_wired_to_main(monkeypatch, tmp_path):
    import app.main as main
    from app import spot_future_paper_store as storage

    async def go():
        path = str(tmp_path / "d.db")
        await storage.init(path)
        monkeypatch.setattr(main, "config", NS(db_path=path, interval=30))
        text = await main.text_for("sf_replay")
        assert "Подходящих сделок" in text
        callbacks = [
            x.callback_data
            for row in main.keyboard_for("sf_replay").inline_keyboard
            for x in row
        ]
        assert "replay" in callbacks and "sf_replay" in callbacks
        assert (
            main.keyboard_for("live_positions").inline_keyboard[0][-1].callback_data
            == "live"
        )

    asyncio.run(go())


def test_paused_secondary_keeps_position_service_running_without_new_entries():
    from app.secondary_strategy_runtime import SecondaryRuntime

    async def go():
        seen = []

        class Watching:
            paper = object()

            async def cycle(self):
                seen.append(self.entry_enabled)
                return []

        class Discovery:
            async def cycle(self):
                raise AssertionError("discovery must be paused")

        async def record(*args):
            pass

        runtime = StrategyRuntime()
        secondary = SecondaryRuntime(runtime, record, "unused", interval=0.005)
        secondary.paused = lambda: True
        secondary.add("spot_futures", Watching())
        secondary.add("spot_spot", Discovery())
        await secondary.start()
        await asyncio.sleep(0.02)
        await secondary.stop()
        assert seen and not any(seen) and runtime.counts()["spot_spot"] == 0

    asyncio.run(go())


def test_deleted_screen_falls_back_without_hiding_other_api_errors():
    from app.tg_safe_edit import edit
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageText
    import pytest

    async def go():
        class Message:
            reason = "message to edit not found"
            answers = []

            async def edit_text(self, *args, **kwargs):
                raise TelegramBadRequest(
                    EditMessageText(text="x", chat_id=1, message_id=1), self.reason
                )

            async def answer(self, text, **kwargs):
                self.answers.append(text)

        message = Message()
        await edit(message, "restored", main_menu())
        assert message.answers == ["restored"]
        message.reason = "message is not modified"
        await edit(message, "restored")
        assert message.answers == ["restored"]
        message.reason = "invalid HTML"
        with pytest.raises(TelegramBadRequest):
            await edit(message, "broken")

    asyncio.run(go())
