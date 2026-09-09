"""Setup window: connect agents, run the doctor, find the command-line tool.

Everything the app needs for first-run setup lives here, so someone who
installed the notarized .app never has to open a terminal. Adapter calls and
doctor checks shell out and can take seconds, so they run on a worker thread
and come back through queued signals, exactly like the camera preview does.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QFont, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from taprivo.adapters import make_claude_adapter, make_cursor_adapter
from taprivo.adapters.base import AgentAdapter, Check, SetupError, SetupOptions, SetupPlan
from taprivo.config import Config
from taprivo.doctor import overall_ok, run_checks
from taprivo.ui.theme import Palette
from taprivo.ui.widgets import StatusDot, StatusKind

log = logging.getLogger(__name__)

WINDOW_MIN_W, WINDOW_MIN_H = 640, 520
AGENT_TITLES = {"claude": "Claude Code", "cursor": "Cursor"}
DOCTOR_COLUMNS = ("Check", "Status", "Detail", "Hint")
CHECK_KIND: dict[str, StatusKind] = {"ok": "ok", "warn": "warn", "fail": "err"}
CHECKING_TEXT = "Checking…"
BUSY_TEXT = {"connect": "Connecting…", "disconnect": "Disconnecting…"}
CLI_HINT = "Run `{path} --help` in Terminal for every command."

ChecksRunner = Callable[..., list[Check]]


@dataclass(frozen=True)
class AgentStatus:
    kind: StatusKind
    text: str


@dataclass(frozen=True)
class AgentOutcome:
    """What one Connect or Disconnect run produced, ready for the GUI thread."""

    key: str
    log: str
    ok: bool
    statuses: dict[str, AgentStatus]


CONNECTED = AgentStatus("ok", "Connected")
NOT_CONNECTED = AgentStatus("warn", "Not connected")
NOT_INSTALLED = AgentStatus("off", "Not installed")
CHECKING = AgentStatus("off", CHECKING_TEXT)


def default_adapters(config: Config) -> dict[str, AgentAdapter]:
    return {"claude": make_claude_adapter(config), "cursor": make_cursor_adapter(config)}


def agent_status(adapter: AgentAdapter) -> AgentStatus:
    """Connected / Not connected / Not installed, from the adapter itself."""
    if not adapter.detect().found:
        return NOT_INSTALLED
    registered = any(
        check.name.endswith("_registration") and check.status == "ok" for check in adapter.verify()
    )
    return CONNECTED if registered else NOT_CONNECTED


def apply_plan(plan: SetupPlan) -> str:
    """Apply every action and return the messages people should read."""
    lines = [action.apply() for action in plan.actions]
    lines.extend(plan.notes)
    return "\n".join(line for line in lines if line) or "Nothing to do."


def cli_entry_point() -> str:
    """The command-line tool for this install: the bundle's own executable when
    frozen, otherwise the `taprivo` next to the interpreter running us."""
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return str(executable)
    sibling = executable.with_name("taprivo")
    return str(sibling) if sibling.exists() else "taprivo"


def _cell(text: str) -> QTableWidgetItem:
    """A read-only cell that keeps its full text in a tooltip: detail and hint
    lines are longer than any sensible column width."""
    item = QTableWidgetItem(text)
    item.setToolTip(text)
    return item


def _card(title: str, palette: Palette) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    heading = QLabel(title)
    font = QFont()
    font.setPixelSize(12)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
    heading.setFont(font)
    heading.setStyleSheet(f"color: {palette.text_dim};")
    layout.addWidget(heading)
    return frame, layout


class SetupSignals(QObject):
    """Owned by Python, not by the window, so a worker that outlives a closed
    window still has a live object to emit on."""

    agent_finished = Signal(object)
    statuses_ready = Signal(object)
    checks_ready = Signal(object)


class AgentRow(QWidget):
    """One agent: its state, the two actions, and what the last run said."""

    def __init__(self, title: str, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self.name_label = QLabel(title)
        name_font = QFont()
        name_font.setWeight(QFont.Weight.DemiBold)
        self.name_label.setFont(name_font)
        self.name_label.setFixedWidth(120)
        self.status = StatusDot(palette=palette)
        self.status.setStatus(CHECKING.kind, CHECKING.text)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("primary")
        self.disconnect_button = QPushButton("Disconnect")
        self.log_label = QLabel("")
        self.log_label.setWordWrap(True)
        self.log_label.setStyleSheet(f"color: {palette.text_dim}; font-size: 11px;")
        self.log_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.name_label)
        head.addWidget(self.status, 1)
        head.addWidget(self.connect_button)
        head.addWidget(self.disconnect_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(head)
        layout.addWidget(self.log_label)

    def setStatus(self, status: AgentStatus) -> None:  # noqa: N802 (Qt naming)
        self.status.setStatus(status.kind, status.text)

    def setLog(self, text: str, *, error: bool = False) -> None:  # noqa: N802 (Qt naming)
        color = self._palette.err if error else self._palette.text_dim
        self.log_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self.log_label.setText(text)


class SetupWindow(QWidget):
    def __init__(
        self,
        config: Config,
        *,
        palette: Palette,
        adapters: dict[str, AgentAdapter] | None = None,
        checks_runner: ChecksRunner = run_checks,
    ) -> None:
        super().__init__()
        self._config = config
        self._palette = palette
        self._adapters = adapters if adapters is not None else default_adapters(config)
        self._checks_runner = checks_runner
        self._busy = False

        self.signals = SetupSignals()
        self.signals.agent_finished.connect(
            self.on_agent_finished, Qt.ConnectionType.QueuedConnection
        )
        self.signals.statuses_ready.connect(
            self.on_statuses_ready, Qt.ConnectionType.QueuedConnection
        )
        self.signals.checks_ready.connect(self.on_checks_ready, Qt.ConnectionType.QueuedConnection)

        self.setWindowTitle("Taprivo Setup")
        self.setMinimumSize(WINDOW_MIN_W, WINDOW_MIN_H)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self._build_agents_card())
        layout.addWidget(self._build_doctor_card(), 1)
        layout.addWidget(self._build_cli_card())

    # -- construction --------------------------------------------------------

    def _build_agents_card(self) -> QFrame:
        card, card_layout = _card("AGENTS", self._palette)
        self.agent_rows: dict[str, AgentRow] = {}
        for key in self._adapters:
            row = AgentRow(AGENT_TITLES.get(key, key.title()), self._palette)
            row.connect_button.clicked.connect(lambda _=False, k=key: self.connect_agent(k))
            row.disconnect_button.clicked.connect(lambda _=False, k=key: self.disconnect_agent(k))
            self.agent_rows[key] = row
            card_layout.addWidget(row)
        return card

    def _build_doctor_card(self) -> QFrame:
        card, card_layout = _card("DOCTOR", self._palette)
        self.doctor_button = QPushButton("Run doctor")
        self.doctor_button.clicked.connect(self.run_doctor)
        self.probe_checkbox = QCheckBox("with camera probe")
        self.doctor_summary = QLabel("Not run yet.")
        self.doctor_summary.setStyleSheet(f"color: {self._palette.text_dim};")

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.doctor_button)
        head.addWidget(self.probe_checkbox)
        head.addStretch(1)
        head.addWidget(self.doctor_summary)

        self.doctor_table = QTableWidget(0, len(DOCTOR_COLUMNS))
        self.doctor_table.setHorizontalHeaderLabels(list(DOCTOR_COLUMNS))
        self.doctor_table.verticalHeader().setVisible(False)
        self.doctor_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.doctor_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.doctor_table.setAlternatingRowColors(False)
        self.doctor_table.setWordWrap(False)
        header = self.doctor_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.doctor_table.setColumnWidth(1, 92)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        card_layout.addLayout(head)
        card_layout.addWidget(self.doctor_table, 1)
        return card

    def _build_cli_card(self) -> QFrame:
        card, card_layout = _card("COMMAND LINE", self._palette)
        path = cli_entry_point()
        self.cli_field = QLineEdit(path)
        self.cli_field.setReadOnly(True)
        self.cli_field.setCursorPosition(0)
        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self.copy_cli_path)
        self.cli_hint = QLabel(CLI_HINT.format(path=path))
        self.cli_hint.setWordWrap(True)
        self.cli_hint.setStyleSheet(f"color: {self._palette.text_dim}; font-size: 11px;")

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.cli_field, 1)
        row.addWidget(self.copy_button)
        card_layout.addLayout(row)
        card_layout.addWidget(self.cli_hint)
        return card

    # -- lifecycle -----------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt override)
        """Re-read the agent state every time the window opens: someone may
        have connected an agent from a terminal since it was last closed."""
        super().showEvent(event)
        if not self._busy:
            self.refresh_statuses()

    # -- worker --------------------------------------------------------------

    def _start(self, name: str, work: Callable[[], None]) -> None:
        threading.Thread(target=work, name=name, daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        """One job at a time: every action button follows the same flag, so a
        slow `claude` call cannot be started twice or raced by a doctor run."""
        self._busy = busy
        for row in self.agent_rows.values():
            row.connect_button.setEnabled(not busy)
            row.disconnect_button.setEnabled(not busy)
        self.doctor_button.setEnabled(not busy)

    def _statuses(self) -> dict[str, AgentStatus]:
        statuses: dict[str, AgentStatus] = {}
        for key, adapter in self._adapters.items():
            try:
                statuses[key] = agent_status(adapter)
            except Exception:  # a broken adapter must not take the window down
                log.exception("status check failed for %s", key)
                statuses[key] = NOT_INSTALLED
        return statuses

    @Slot()
    def refresh_statuses(self) -> None:
        signals = self.signals

        def work() -> None:
            signals.statuses_ready.emit(self._statuses())

        self._start("taprivo-setup-status", work)

    def _run_agent(self, key: str, action: str) -> None:
        if self._busy:
            return
        row = self.agent_rows[key]
        row.setLog(BUSY_TEXT[action])
        row.setStatus(CHECKING)
        self._set_busy(True)
        adapter = self._adapters[key]
        signals = self.signals

        def work() -> None:
            ok = True
            try:
                plan = (
                    adapter.plan_setup(SetupOptions(install_instructions=True))
                    if action == "connect"
                    else adapter.plan_remove(SetupOptions())
                )
                text = apply_plan(plan)
            except SetupError as exc:
                text, ok = str(exc), False
            except Exception as exc:  # never lose the window to an adapter bug
                log.exception("%s failed for %s", action, key)
                text, ok = str(exc), False
            signals.agent_finished.emit(AgentOutcome(key, text, ok, self._statuses()))

        self._start(f"taprivo-setup-{action}", work)

    @Slot(str)
    def connect_agent(self, key: str) -> None:
        self._run_agent(key, "connect")

    @Slot(str)
    def disconnect_agent(self, key: str) -> None:
        self._run_agent(key, "disconnect")

    @Slot()
    def run_doctor(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self.doctor_summary.setText("Running…")
        probe = self.probe_checkbox.isChecked()
        config = self._config
        runner = self._checks_runner
        signals = self.signals

        def work() -> None:
            try:
                # The app itself holds the instance lock; say so instead of
                # letting the doctor conclude another Taprivo is running.
                checks = runner(config, camera_probe=probe, lock_held=True)
            except Exception as exc:  # a failed run must read as one failed check
                log.exception("doctor run failed")
                checks = [Check("doctor", "fail", str(exc), "run 'taprivo doctor' in a terminal")]
            signals.checks_ready.emit(list(checks))

        self._start("taprivo-setup-doctor", work)

    # -- results (GUI thread) ------------------------------------------------

    @Slot(object)
    def on_statuses_ready(self, statuses: object) -> None:
        if self._busy:
            return  # a running job will deliver fresher statuses when it ends
        if isinstance(statuses, dict):
            self._apply_statuses(statuses)

    def _apply_statuses(self, statuses: dict[str, AgentStatus]) -> None:
        for key, status in statuses.items():
            row = self.agent_rows.get(key)
            if row is not None:
                row.setStatus(status)

    @Slot(object)
    def on_agent_finished(self, outcome: object) -> None:
        if not isinstance(outcome, AgentOutcome):
            return
        row = self.agent_rows[outcome.key]
        row.setLog(outcome.log, error=not outcome.ok)
        self._apply_statuses(outcome.statuses)
        self._set_busy(False)

    @Slot(object)
    def on_checks_ready(self, checks: object) -> None:
        if not isinstance(checks, list):
            return
        self._fill_table(checks)
        problems = sum(1 for check in checks if check.status == "fail")
        if overall_ok(checks):
            self.doctor_summary.setText("All checks passed")
            self.doctor_summary.setStyleSheet(f"color: {self._palette.ok};")
        else:
            plural = "" if problems == 1 else "s"
            self.doctor_summary.setText(f"{problems} problem{plural}")
            self.doctor_summary.setStyleSheet(f"color: {self._palette.err};")
        self._set_busy(False)

    def _fill_table(self, checks: Sequence[Check]) -> None:
        self.doctor_table.setRowCount(len(checks))
        for row, check in enumerate(checks):
            self.doctor_table.setItem(row, 0, _cell(check.name))
            dot = StatusDot(palette=self._palette)
            dot.setStatus(CHECK_KIND[check.status], check.status.upper())
            self.doctor_table.setCellWidget(row, 1, dot)
            self.doctor_table.setItem(row, 2, _cell(check.detail))
            self.doctor_table.setItem(row, 3, _cell(check.hint))

    # -- command line --------------------------------------------------------

    @Slot()
    def copy_cli_path(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.cli_field.text())
