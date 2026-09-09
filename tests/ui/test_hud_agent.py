"""The HUD's Claude Code card: no payload, a fresh one, and a stale one."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pytestqt.qtbot import QtBot

from taprivo.core.state import AgentStatus
from taprivo.ui.hud import AGENT_HINT, AGENT_NO_LIMITS
from taprivo.ui.theme import DARK
from tests.ui.conftest import HudBundle, build_hud

#: A fixed wall clock, so "resets in" and "last update" are exact.
NOW = 1_757_000_000.0


def status(**overrides: object) -> AgentStatus:
    fields: dict[str, object] = {
        "model": "Opus 4.6",
        "context_used": 63.4,
        "cost_usd": 1.4237,
        "duration_s": 2880,
        "five_hour_used": 42.0,
        "five_hour_resets_at": int(NOW) + 8100,  # 2 h 15 m
        "seven_day_used": 71.5,
        "seven_day_resets_at": int(NOW) + 273_600,  # 3 d 4 h
        "version": "2.1.0",
        "received_at_ms": int(NOW * 1000),
    }
    fields.update(overrides)
    return AgentStatus(**fields)  # type: ignore[arg-type]


@pytest.fixture
def hud(qtbot: QtBot) -> Iterator[HudBundle]:
    # A fixed palette as well as a fixed clock: "system" would follow whatever
    # colour scheme the machine running the tests happens to report.
    yield build_hud(qtbot, clock=lambda: NOW, palette=DARK)


def report(hud: HudBundle, qtbot: QtBot, agent: AgentStatus) -> None:
    """Report a status and wait for the card to draw exactly that one."""
    hud.engine.set_agent_status(agent)
    qtbot.waitUntil(lambda: hud.window.rendered_agent() is agent, timeout=2000)


# -- nothing reported yet ---------------------------------------------------


def test_without_a_payload_the_card_is_one_line_of_advice(hud: HudBundle) -> None:
    window = hud.window
    assert window.agent_card.isVisible()
    assert window.agent_hint_label.isVisible()
    assert window.agent_hint_label.text() == AGENT_HINT
    assert "Setup…" in AGENT_HINT
    assert not window.agent_model_label.isVisible()
    assert not window.agent_context_row.isVisible()
    assert not window.agent_rows["five_hour"].isVisible()
    assert not window.agent_rows["seven_day"].isVisible()
    assert not window.agent_footer_label.isVisible()


# -- a fresh payload with limits --------------------------------------------


def test_a_fresh_payload_fills_every_row(hud: HudBundle, qtbot: QtBot) -> None:
    report(hud, qtbot, status())
    window = hud.window
    assert window.agent_model_label.text() == "Opus 4.6"
    assert window.agent_context_label.text() == "Context 63 %"
    assert window.agent_context_bar.value() == 63
    assert window.agent_five_hour_bar.value() == 42
    assert window.agent_seven_day_bar.value() == 72
    assert window.agent_reset_labels["five_hour"].text() == "resets in 2 h 15 m"
    assert window.agent_reset_labels["seven_day"].text() == "resets in 3 d 4 h"
    assert window.agent_footer_label.text() == "$1.42 · 48 min"


def test_limit_bars_are_coloured_by_how_much_is_left(hud: HudBundle, qtbot: QtBot) -> None:
    report(hud, qtbot, status(five_hour_used=69.9, seven_day_used=90.0))
    window = hud.window
    assert window.agent_five_hour_bar.accent().lower() == DARK.ok.lower()
    assert window.agent_seven_day_bar.accent().lower() == DARK.err.lower()
    report(hud, qtbot, status(five_hour_used=70.0))
    assert window.agent_five_hour_bar.accent().lower() == DARK.warn.lower()


def test_a_window_that_has_run_out_says_so(hud: HudBundle, qtbot: QtBot) -> None:
    report(hud, qtbot, status(five_hour_resets_at=int(NOW) - 5, seven_day_resets_at=int(NOW) + 30))
    assert hud.window.agent_reset_labels["five_hour"].text() == "resetting now"
    assert hud.window.agent_reset_labels["seven_day"].text() == "resets in under a minute"


# -- stale, and without a subscription --------------------------------------


def test_a_stale_card_dims_and_says_how_old_it_is(hud: HudBundle, qtbot: QtBot) -> None:
    report(hud, qtbot, status(received_at_ms=int((NOW - 180) * 1000)))
    window = hud.window
    assert window.agent_footer_label.text() == "last update 3 min ago"
    # Dimming is a blend towards the card, not an opacity effect: the colour
    # itself must have moved off the palette.
    assert window.agent_five_hour_bar.accent().lower() != DARK.ok.lower()
    assert DARK.text_dim.lower() not in window.agent_model_label.styleSheet().lower()


def test_a_fresh_card_is_not_dimmed(hud: HudBundle, qtbot: QtBot) -> None:
    report(hud, qtbot, status(received_at_ms=int((NOW - 89) * 1000)))
    assert hud.window.agent_footer_label.text() == "$1.42 · 48 min"
    assert DARK.text_dim.lower() in hud.window.agent_model_label.styleSheet().lower()


def test_an_api_key_user_is_told_the_limits_are_not_theirs(hud: HudBundle, qtbot: QtBot) -> None:
    report(
        hud,
        qtbot,
        status(
            five_hour_used=None,
            five_hour_resets_at=None,
            seven_day_used=None,
            seven_day_resets_at=None,
        ),
    )
    window = hud.window
    assert window.agent_reset_labels["five_hour"].text() == AGENT_NO_LIMITS
    assert not window.agent_five_hour_bar.isVisible()
    assert not window.agent_seven_day_bar.isVisible()
    assert not window.agent_rows["seven_day"].isVisible()
    # The rest of the card still works.
    assert window.agent_context_bar.value() == 63
    assert window.agent_footer_label.text() == "$1.42 · 48 min"


def test_limits_come_back_after_a_payload_without_them(hud: HudBundle, qtbot: QtBot) -> None:
    report(hud, qtbot, status(five_hour_used=None, seven_day_used=None))
    report(hud, qtbot, status())
    window = hud.window
    assert window.agent_rows["seven_day"].isVisible()
    assert window.agent_five_hour_bar.isVisible()
    assert window.agent_reset_labels["five_hour"].text() == "resets in 2 h 15 m"


def test_a_payload_with_only_a_model_hides_what_it_cannot_show(
    hud: HudBundle, qtbot: QtBot
) -> None:
    report(
        hud,
        qtbot,
        AgentStatus(
            model="Sonnet",
            context_used=None,
            cost_usd=None,
            duration_s=None,
            five_hour_used=None,
            five_hour_resets_at=None,
            seven_day_used=None,
            seven_day_resets_at=None,
            version=None,
            received_at_ms=int(NOW * 1000),
        ),
    )
    window = hud.window
    assert window.agent_model_label.text() == "Sonnet"
    assert not window.agent_context_row.isVisible()
    assert not window.agent_footer_label.isVisible()


def test_a_reset_session_keeps_the_card(hud: HudBundle, qtbot: QtBot) -> None:
    """The agent's report is not session data; a reset must not clear it."""
    report(hud, qtbot, status())
    hud.engine.reset_session()
    qtbot.waitUntil(lambda: hud.window.agent_model_label.text() == "Opus 4.6", timeout=2000)
