from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from taprivo.adapters.base import (
    Check,
    DetectResult,
    SetupAction,
    SetupError,
    SetupOptions,
    SetupPlan,
)
from taprivo.config import Config
from taprivo.ui.setup_window import SetupWindow, cli_entry_point
from taprivo.ui.theme import DARK

CHECKS = [
    Check("endpoint", "ok", "app reachable at http://127.0.0.1:32145/mcp"),
    Check("camera_devices", "warn", "no camera devices found", "connect a camera"),
    Check("claude_registration", "fail", "no 'taprivo' MCP server", "run 'taprivo setup claude'"),
]


class FakeAdapter:
    """Records what the window asked it to do and answers with fixed state."""

    def __init__(
        self,
        name: str,
        *,
        installed: bool = True,
        registered: bool = False,
        error: str | None = None,
    ) -> None:
        self.name = name
        self.installed = installed
        self.registered = registered
        self.error = error
        self.setup_options: list[SetupOptions] = []
        self.remove_options: list[SetupOptions] = []

    def detect(self) -> DetectResult:
        return DetectResult(self.installed, "1.0", "/usr/local/bin/fake")

    def _plan(self, message: str) -> SetupPlan:
        def run() -> str:
            if self.error is not None:
                raise SetupError(self.error)
            return message

        return SetupPlan(actions=[SetupAction("kind", "description", run)], notes=["a note"])

    def plan_setup(self, options: SetupOptions) -> SetupPlan:
        self.setup_options.append(options)
        plan = self._plan("registered 'taprivo'")
        if self.error is None:
            self.registered = True
        return plan

    def plan_remove(self, options: SetupOptions) -> SetupPlan:
        self.remove_options.append(options)
        plan = self._plan("removed 'taprivo'")
        if self.error is None:
            self.registered = False
        return plan

    def verify(self) -> list[Check]:
        status = "ok" if self.registered else "fail"
        return [Check(f"{self.name}_registration", status, "detail")]


class FakeRunner:
    """Stands in for doctor.run_checks and remembers how it was called."""

    def __init__(self, checks: list[Check] | None = None) -> None:
        self.checks = CHECKS if checks is None else checks
        self.calls: list[dict[str, object]] = []

    def __call__(
        self, config: Config, *, camera_probe: bool = False, lock_held: bool | None = None
    ) -> list[Check]:
        self.calls.append({"camera_probe": camera_probe, "lock_held": lock_held})
        return list(self.checks)


# -- helpers ----------------------------------------------------------------


class _Gate:
    """A latch a worker waits on, so a test can observe the running state."""

    def __init__(self) -> None:
        import threading

        self._event = threading.Event()

    def wait(self) -> None:
        assert self._event.wait(timeout=5)

    def open(self) -> None:
        self._event.set()


class _GatedAdapter(FakeAdapter):
    def __init__(self, name: str, gate: _Gate) -> None:
        super().__init__(name)
        self._gate = gate

    def plan_setup(self, options: SetupOptions) -> SetupPlan:
        plan = super().plan_setup(options)
        actions = [SetupAction("kind", "description", self._blocked)]
        return SetupPlan(actions=actions, notes=plan.notes)

    def _blocked(self) -> str:
        self._gate.wait()
        return "registered 'taprivo'"


class _GatedRunner(FakeRunner):
    def __init__(self, gate: _Gate) -> None:
        super().__init__()
        self._gate = gate

    def __call__(
        self, config: Config, *, camera_probe: bool = False, lock_held: bool | None = None
    ) -> list[Check]:
        self._gate.wait()
        return super().__call__(config, camera_probe=camera_probe, lock_held=lock_held)


def build_window(
    qtbot: QtBot,
    adapters: dict[str, object] | None = None,
    runner: FakeRunner | None = None,
) -> tuple[SetupWindow, dict[str, FakeAdapter], FakeRunner]:
    agents = adapters or {"claude": FakeAdapter("claude"), "cursor": FakeAdapter("cursor")}
    checks_runner = runner or FakeRunner()
    window = SetupWindow(
        Config(),
        palette=DARK,
        adapters=agents,  # type: ignore[arg-type]
        checks_runner=checks_runner,
    )
    qtbot.addWidget(window)
    window.show()
    return window, agents, checks_runner  # type: ignore[return-value]


def wait_for_status(qtbot: QtBot, window: SetupWindow, key: str, text: str) -> None:
    qtbot.waitUntil(lambda: window.agent_rows[key].status.text() == text, timeout=2000)


# -- agents -----------------------------------------------------------------


def test_statuses_are_read_from_the_adapters(qtbot: QtBot) -> None:
    adapters = {
        "claude": FakeAdapter("claude", registered=True),
        "cursor": FakeAdapter("cursor", installed=False),
    }
    window, _, _ = build_window(qtbot, adapters)  # type: ignore[arg-type]
    wait_for_status(qtbot, window, "claude", "Connected")
    wait_for_status(qtbot, window, "cursor", "Not installed")


def test_connect_applies_the_plan_and_reports_it(qtbot: QtBot) -> None:
    window, adapters, _ = build_window(qtbot)
    wait_for_status(qtbot, window, "claude", "Not connected")
    window.agent_rows["claude"].connect_button.click()
    wait_for_status(qtbot, window, "claude", "Connected")
    assert adapters["claude"].setup_options == [
        SetupOptions(install_instructions=True, statusline=True)
    ]
    log = window.agent_rows["claude"].log_label.text()
    assert "registered 'taprivo'" in log
    assert "a note" in log
    assert window.agent_rows["cursor"].status.text() == "Not connected"


def test_the_status_line_checkbox_is_on_by_default_and_reaches_the_adapter(
    qtbot: QtBot,
) -> None:
    window, adapters, _ = build_window(qtbot)
    checkbox = window.agent_rows["claude"].option_checkbox
    assert checkbox is not None
    assert checkbox.isChecked()
    assert "status line" in checkbox.text()
    # Cursor has no status line of its own, so its row carries no checkbox.
    assert window.agent_rows["cursor"].option_checkbox is None
    checkbox.setChecked(False)
    wait_for_status(qtbot, window, "claude", "Not connected")
    window.agent_rows["claude"].connect_button.click()
    wait_for_status(qtbot, window, "claude", "Connected")
    assert adapters["claude"].setup_options[-1].statusline is False


def test_disconnect_runs_the_removal_plan(qtbot: QtBot) -> None:
    adapters = {"claude": FakeAdapter("claude", registered=True)}
    window, _, _ = build_window(qtbot, adapters)  # type: ignore[arg-type]
    wait_for_status(qtbot, window, "claude", "Connected")
    window.agent_rows["claude"].disconnect_button.click()
    wait_for_status(qtbot, window, "claude", "Not connected")
    assert adapters["claude"].remove_options == [SetupOptions()]
    assert "removed 'taprivo'" in window.agent_rows["claude"].log_label.text()


def test_a_setup_error_is_shown_and_leaves_the_window_usable(qtbot: QtBot) -> None:
    adapters = {"claude": FakeAdapter("claude", error="claude mcp add failed: no such command")}
    window, _, _ = build_window(qtbot, adapters)  # type: ignore[arg-type]
    wait_for_status(qtbot, window, "claude", "Not connected")
    window.agent_rows["claude"].connect_button.click()
    qtbot.waitUntil(
        lambda: "no such command" in window.agent_rows["claude"].log_label.text(), timeout=2000
    )
    assert window.agent_rows["claude"].connect_button.isEnabled()
    assert DARK.err in window.agent_rows["claude"].log_label.styleSheet()


def test_buttons_are_disabled_while_a_connect_runs(qtbot: QtBot) -> None:
    release = _Gate()
    adapters = {"claude": _GatedAdapter("claude", release)}
    window, _, _ = build_window(qtbot, adapters)  # type: ignore[arg-type]
    window.agent_rows["claude"].connect_button.click()
    qtbot.waitUntil(lambda: not window.agent_rows["claude"].connect_button.isEnabled())
    assert not window.agent_rows["claude"].disconnect_button.isEnabled()
    assert not window.doctor_button.isEnabled()
    release.open()
    qtbot.waitUntil(lambda: window.agent_rows["claude"].connect_button.isEnabled(), timeout=2000)
    assert window.doctor_button.isEnabled()


# -- doctor -----------------------------------------------------------------


def test_run_doctor_fills_the_table(qtbot: QtBot) -> None:
    window, _, runner = build_window(qtbot)
    window.doctor_button.click()
    qtbot.waitUntil(lambda: window.doctor_table.rowCount() == len(CHECKS), timeout=2000)
    assert window.doctor_table.item(0, 0).text() == "endpoint"
    assert window.doctor_table.cellWidget(0, 1).text() == "OK"
    assert window.doctor_table.cellWidget(1, 1).text() == "WARN"
    assert window.doctor_table.item(1, 2).text() == "no camera devices found"
    assert window.doctor_table.item(2, 3).text() == "run 'taprivo setup claude'"
    assert window.doctor_summary.text() == "1 problem"
    assert runner.calls == [{"camera_probe": False, "lock_held": True}]


def test_the_probe_checkbox_reaches_the_runner(qtbot: QtBot) -> None:
    window, _, runner = build_window(qtbot)
    window.probe_checkbox.setChecked(True)
    window.doctor_button.click()
    qtbot.waitUntil(lambda: runner.calls != [], timeout=2000)
    assert runner.calls == [{"camera_probe": True, "lock_held": True}]


def test_a_healthy_run_reports_all_checks_passed(qtbot: QtBot) -> None:
    runner = FakeRunner([Check("endpoint", "ok", "reachable"), Check("camera", "warn", "none")])
    window, _, _ = build_window(qtbot, runner=runner)
    window.doctor_button.click()
    qtbot.waitUntil(lambda: window.doctor_summary.text() == "All checks passed", timeout=2000)
    assert window.doctor_table.rowCount() == 2


def test_several_failures_are_counted(qtbot: QtBot) -> None:
    runner = FakeRunner([Check("a", "fail", "x"), Check("b", "fail", "y"), Check("c", "ok", "z")])
    window, _, _ = build_window(qtbot, runner=runner)
    window.doctor_button.click()
    qtbot.waitUntil(lambda: window.doctor_summary.text() == "2 problems", timeout=2000)


def test_the_doctor_button_is_disabled_while_it_runs(qtbot: QtBot) -> None:
    release = _Gate()
    runner = _GatedRunner(release)
    window, _, _ = build_window(qtbot, runner=runner)  # type: ignore[arg-type]
    window.doctor_button.click()
    qtbot.waitUntil(lambda: not window.doctor_button.isEnabled())
    assert not window.agent_rows["claude"].connect_button.isEnabled()
    release.open()
    qtbot.waitUntil(lambda: window.doctor_button.isEnabled(), timeout=2000)


# -- command line -----------------------------------------------------------


def test_cli_field_points_at_the_venv_entry_point(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").touch()
    entry = bin_dir / "taprivo"
    entry.touch()
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    window, _, _ = build_window(qtbot)
    assert window.cli_field.text() == str(entry)
    assert window.cli_field.isReadOnly()
    assert f"{entry} --help" in window.cli_hint.text()


def test_cli_field_points_at_the_bundle_when_frozen(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "Taprivo.app" / "Contents" / "MacOS" / "Taprivo"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    window, _, _ = build_window(qtbot)
    assert window.cli_field.text() == str(executable)


def test_cli_entry_point_falls_back_to_the_command_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    assert cli_entry_point() == "taprivo"


def test_copy_puts_the_path_on_the_clipboard(qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QApplication

    window, _, _ = build_window(qtbot)
    window.copy_button.click()
    assert QApplication.clipboard().text() == window.cli_field.text()


def test_reopening_the_window_re_reads_the_agent_state(qtbot: QtBot) -> None:
    adapters = {"claude": FakeAdapter("claude")}
    window, _, _ = build_window(qtbot, adapters)  # type: ignore[arg-type]
    wait_for_status(qtbot, window, "claude", "Not connected")
    adapters["claude"].registered = True  # e.g. 'taprivo setup claude' in a terminal
    window.close()
    window.show()
    wait_for_status(qtbot, window, "claude", "Connected")
