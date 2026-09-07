from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from taprivo import cli, paths
from taprivo.adapters.claude import ClaudeAdapter
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
