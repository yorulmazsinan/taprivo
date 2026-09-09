from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from taprivo import cli, paths
from taprivo.adapters.claude import ClaudeAdapter
from taprivo.adapters.cursor import CursorAdapter
from taprivo.config import Config
from tests.unit.test_claude_adapter import FakeClaude

runner = CliRunner()


@pytest.fixture
def fake(
    taprivo_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FakeClaude, Path]:
    fake_runner = FakeClaude()
    home = tmp_path / "home"
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)

    def factory(config: Config) -> ClaudeAdapter:
        return ClaudeAdapter(config, home=home, runner=fake_runner, claude_bin=str(script))

    monkeypatch.setattr(cli, "make_claude_adapter", factory)
    return fake_runner, home


def test_dry_run_prints_plan_and_writes_nothing(fake: tuple[FakeClaude, Path]) -> None:
    fake_runner, home = fake
    result = runner.invoke(cli.app, ["setup", "claude", "--dry-run", "--install-instructions"])
    assert result.exit_code == 0, result.output
    assert "Register HTTP MCP server" in result.output
    assert "Dry run" in result.output
    assert not (home / ".claude").exists()
    assert not paths.token_path().exists()
    assert fake_runner.registered_url is None


def test_setup_applies_and_hides_token(fake: tuple[FakeClaude, Path]) -> None:
    fake_runner, home = fake
    result = runner.invoke(cli.app, ["setup", "claude"])
    assert result.exit_code == 0, result.output
    token = paths.token_path().read_text().strip()
    assert token not in result.output
    assert fake_runner.registered_url == "http://127.0.0.1:32145/mcp"
    assert (home / ".claude" / "taprivo.md").exists()
    assert "--install-instructions" in result.output


def test_setup_fails_without_claude(taprivo_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "make_claude_adapter",
        lambda config: ClaudeAdapter(config, claude_bin="/nonexistent/claude"),
    )
    result = runner.invoke(cli.app, ["setup", "claude"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_remove(fake: tuple[FakeClaude, Path]) -> None:
    fake_runner, home = fake
    runner.invoke(cli.app, ["setup", "claude", "--install-instructions"])
    result = runner.invoke(cli.app, ["remove", "claude"])
    assert result.exit_code == 0, result.output
    assert fake_runner.registered_url is None
    assert not (home / ".claude" / "taprivo.md").exists()
    again = runner.invoke(cli.app, ["remove", "claude"])
    assert again.exit_code == 0 and "Nothing to remove" in again.output


@pytest.fixture
def cursor_home(taprivo_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "cursor-home"

    def factory(config: Config) -> CursorAdapter:
        return CursorAdapter(config, home=home, cursor_bin="/nonexistent/cursor")

    monkeypatch.setattr(cli, "make_cursor_adapter", factory)
    return home


def test_setup_cursor_dry_run_writes_nothing(cursor_home: Path) -> None:
    result = runner.invoke(cli.app, ["setup", "cursor", "--dry-run", "--install-instructions"])
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert "Merge server 'taprivo' into" in result.output
    assert not cursor_home.exists()
    assert not paths.token_path().exists()


def test_setup_cursor_writes_user_config_and_hides_token(cursor_home: Path) -> None:
    result = runner.invoke(cli.app, ["setup", "cursor", "--install-instructions"])
    assert result.exit_code == 0, result.output
    token = paths.token_path().read_text().strip()
    assert token not in result.output
    entry = json.loads((cursor_home / ".cursor" / "mcp.json").read_text())["mcpServers"]["taprivo"]
    assert entry["url"] == "http://127.0.0.1:32145/mcp"
    assert entry["headers"]["Authorization"] == f"Bearer {token}"
    assert (cursor_home / ".cursor" / "taprivo.md").exists()
    assert "Settings > Rules" in result.output


def test_setup_cursor_project_uses_env_placeholder(
    cursor_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    result = runner.invoke(cli.app, ["setup", "cursor", "--project", "--install-instructions"])
    assert result.exit_code == 0, result.output
    text = (project / ".cursor" / "mcp.json").read_text()
    assert "${env:TAPRIVO_TOKEN}" in text
    assert paths.token_path().read_text().strip() not in text
    assert (project / ".cursor" / "rules" / "taprivo.mdc").exists()


def test_setup_cursor_json_lists_actions(cursor_home: Path) -> None:
    result = runner.invoke(cli.app, ["setup", "cursor", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True and payload["agent"] == "cursor"
    assert payload["dry_run"] is False
    assert [a["kind"] for a in payload["actions"]] == ["ensure_token", "merge_json"]
    assert all(a["summary"] for a in payload["actions"])
    assert paths.token_path().read_text().strip() not in result.output


def test_setup_cursor_json_dry_run_reports_no_summaries(cursor_home: Path) -> None:
    result = runner.invoke(cli.app, ["setup", "cursor", "--json", "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert all(a["summary"] is None for a in payload["actions"])
    assert not cursor_home.exists()


def test_remove_cursor(cursor_home: Path) -> None:
    runner.invoke(cli.app, ["setup", "cursor", "--install-instructions"])
    result = runner.invoke(cli.app, ["remove", "cursor"])
    assert result.exit_code == 0, result.output
    servers = json.loads((cursor_home / ".cursor" / "mcp.json").read_text())["mcpServers"]
    assert "taprivo" not in servers
    assert not (cursor_home / ".cursor" / "taprivo.md").exists()
    again = runner.invoke(cli.app, ["remove", "cursor", "--json"])
    assert again.exit_code == 0
    payload = json.loads(again.output)
    assert payload["actions"] == [] and payload["notes"] == ["Nothing to remove."]


def test_remove_claude_json(fake: tuple[FakeClaude, Path]) -> None:
    fake_runner, _ = fake
    runner.invoke(cli.app, ["setup", "claude", "--install-instructions"])
    result = runner.invoke(cli.app, ["remove", "claude", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["agent"] == "claude"
    assert "unregister_mcp" in [a["kind"] for a in payload["actions"]]
    assert fake_runner.registered_url is None
