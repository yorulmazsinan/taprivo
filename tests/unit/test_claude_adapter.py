from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from taprivo import paths
from taprivo.adapters import claude as claude_adapter
from taprivo.adapters.base import SetupError, SetupOptions
from taprivo.adapters.claude import END_MARKER, IMPORT_LINE, START_MARKER, ClaudeAdapter
from taprivo.config import Config


class FakeClaude:
    """Records `claude` CLI invocations and mimics `mcp get/add/remove`."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.registered_url: str | None = None

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        sub = args[1:]
        if sub == ["--version"]:
            return subprocess.CompletedProcess(args, 0, "2.1.0 (Claude Code)\n", "")
        if sub[:3] == ["mcp", "get", "taprivo"]:
            if self.registered_url is None:
                return subprocess.CompletedProcess(
                    args, 1, "", "No MCP server found with name: taprivo\n"
                )
            return subprocess.CompletedProcess(
                args,
                0,
                f"taprivo:\n  Scope: User\n  Type: http\n  URL: {self.registered_url}\n",
                "",
            )
        if sub[:2] == ["mcp", "add"]:
            self.registered_url = sub[sub.index("taprivo") + 1]
            return subprocess.CompletedProcess(args, 0, "Added HTTP MCP server taprivo\n", "")
        if sub[:2] == ["mcp", "remove"]:
            self.registered_url = None
            return subprocess.CompletedProcess(args, 0, "Removed\n", "")
        return subprocess.CompletedProcess(args, 1, "", "unknown command")


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def adapter(taprivo_home: Path, fake_bin: Path, home: Path) -> tuple[ClaudeAdapter, FakeClaude]:
    runner = FakeClaude()
    return ClaudeAdapter(Config(), home=home, runner=runner, claude_bin=str(fake_bin)), runner


def apply_all(adapter: ClaudeAdapter, options: SetupOptions) -> list[str]:
    return [action.apply() for action in adapter.plan_setup(options).actions]


def test_detect(adapter: tuple[ClaudeAdapter, FakeClaude]) -> None:
    ad, _ = adapter
    detected = ad.detect()
    assert detected.found and detected.version == "2.1.0 (Claude Code)"


def test_detect_missing(taprivo_home: Path, home: Path) -> None:
    ad = ClaudeAdapter(Config(), home=home, runner=FakeClaude(), claude_bin="/nonexistent/claude")
    assert ad.detect().found is False


def test_plan_only_writes_nothing(adapter: tuple[ClaudeAdapter, FakeClaude], home: Path) -> None:
    ad, runner = adapter
    plan = ad.plan_setup(SetupOptions(install_instructions=True))
    assert [a.kind for a in plan.actions] == [
        "ensure_token",
        "register_mcp",
        "write_file",
        "patch_marked_block",
    ]
    assert not (home / ".claude").exists()
    assert not paths.token_path().exists()
    assert all(c[1:3] != ["mcp", "add"] for c in runner.calls)


def test_apply_registers_with_header_and_is_idempotent(
    adapter: tuple[ClaudeAdapter, FakeClaude], home: Path
) -> None:
    ad, runner = adapter
    summaries = apply_all(ad, SetupOptions())
    token = paths.token_path().read_text().strip()
    add_call = next(c for c in runner.calls if c[1:3] == ["mcp", "add"])
    assert add_call[1:] == [
        "mcp",
        "add",
        "--transport",
        "http",
        "--scope",
        "user",
        "taprivo",
        "http://127.0.0.1:32145/mcp",
        "--header",
        f"Authorization: Bearer {token}",
    ]
    assert runner.registered_url == "http://127.0.0.1:32145/mcp"
    assert (home / ".claude" / "taprivo.md").read_text().startswith("# Taprivo work budget")
    assert all(token not in s for s in summaries)
    assert all(token not in a.description for a in ad.plan_setup(SetupOptions()).actions)

    second = ad.plan_setup(SetupOptions())
    assert [a.kind for a in second.actions] == ["ensure_token", "write_file"]
    assert any("already" in n for n in second.notes)
    assert paths.token_path().read_text().strip() == token


def test_reregisters_when_url_differs(adapter: tuple[ClaudeAdapter, FakeClaude]) -> None:
    ad, runner = adapter
    runner.registered_url = "http://127.0.0.1:9/mcp"
    apply_all(ad, SetupOptions())
    kinds = [c[1:3] for c in runner.calls]
    assert ["mcp", "remove"] in kinds and ["mcp", "add"] in kinds
    assert runner.registered_url == "http://127.0.0.1:32145/mcp"


def test_install_instructions_block_with_backup(
    adapter: tuple[ClaudeAdapter, FakeClaude], home: Path
) -> None:
    ad, _ = adapter
    claude_md = home / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    claude_md.write_text("# My rules\n")
    apply_all(ad, SetupOptions(install_instructions=True))
    text = claude_md.read_text()
    assert text.startswith("# My rules\n")
    assert text.count(START_MARKER) == 1 and IMPORT_LINE in text and END_MARKER in text
    backups = list(claude_md.parent.glob("CLAUDE.md.taprivo-backup-*"))
    assert len(backups) == 1 and backups[0].read_text() == "# My rules\n"
    apply_all(ad, SetupOptions(install_instructions=True))
    assert claude_md.read_text().count(START_MARKER) == 1


def test_project_merge_preserves_other_servers(
    adapter: tuple[ClaudeAdapter, FakeClaude], tmp_path: Path
) -> None:
    ad, _ = adapter
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"type": "stdio", "command": "x"}}})
    )
    summaries = apply_all(ad, SetupOptions(project=True, project_dir=project))
    data = json.loads((project / ".mcp.json").read_text())
    assert data["mcpServers"]["other"]["command"] == "x"
    assert data["mcpServers"]["taprivo"] == {
        "type": "http",
        "url": "http://127.0.0.1:32145/mcp",
        "headers": {"Authorization": "Bearer ${TAPRIVO_TOKEN}"},
    }
    assert any("TAPRIVO_TOKEN" in s for s in summaries)
    token = paths.token_path().read_text().strip()
    assert token not in (project / ".mcp.json").read_text()


def test_merge_project_rejects_malformed_json(
    adapter: tuple[ClaudeAdapter, FakeClaude], tmp_path: Path
) -> None:
    ad, _ = adapter
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_text("{not valid json")
    plan = ad.plan_setup(SetupOptions(project=True, project_dir=project))
    merge_action = next(a for a in plan.actions if a.kind == "merge_json")
    with pytest.raises(SetupError, match="not valid JSON"):
        merge_action.apply()


def test_remove_project_rejects_malformed_json(
    adapter: tuple[ClaudeAdapter, FakeClaude], tmp_path: Path
) -> None:
    ad, _ = adapter
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_text("{not valid json")
    plan = ad.plan_remove(SetupOptions(project=True, project_dir=project))
    remove_action = next(a for a in plan.actions if a.kind == "edit_json")
    with pytest.raises(SetupError, match="not valid JSON"):
        remove_action.apply()


def test_remove_deletes_only_taprivo_parts(
    adapter: tuple[ClaudeAdapter, FakeClaude], home: Path, tmp_path: Path
) -> None:
    ad, runner = adapter
    project = tmp_path / "proj"
    project.mkdir()
    claude_md = home / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    claude_md.write_text("# Keep me\n")
    apply_all(ad, SetupOptions(install_instructions=True, project=True, project_dir=project))
    for action in ad.plan_remove(SetupOptions(project=True, project_dir=project)).actions:
        action.apply()
    assert runner.registered_url is None
    assert claude_md.read_text() == "# Keep me\n"
    assert not (home / ".claude" / "taprivo.md").exists()
    assert "taprivo" not in json.loads((project / ".mcp.json").read_text())["mcpServers"]
    assert paths.token_path().exists()


def test_verify_reports_each_check(adapter: tuple[ClaudeAdapter, FakeClaude]) -> None:
    ad, _ = adapter
    before = {c.name: c.status for c in ad.verify()}
    assert before["token"] == "fail"
    assert before["claude_cli"] == "ok"
    assert before["claude_registration"] == "fail"
    assert before["instructions_file"] == "fail"
    assert before["instructions_import"] == "warn"
    apply_all(ad, SetupOptions(install_instructions=True))
    after = {c.name: c.status for c in ad.verify()}
    assert set(after.values()) == {"ok"}


# -- binary resolution ------------------------------------------------------


@pytest.fixture
def home_only_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop /opt/homebrew and /usr/local from the search, so a `claude` that
    happens to be installed on the machine running the tests cannot answer."""
    monkeypatch.setattr(
        claude_adapter,
        "EXTRA_DIRS",
        tuple(entry for entry in claude_adapter.EXTRA_DIRS if not entry.startswith("/")),
    )


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_candidate_dirs_cover_the_usual_install_locations(tmp_path: Path) -> None:
    home = tmp_path / "home"
    dirs = claude_adapter.candidate_dirs(home)
    assert dirs[:6] == [
        home / ".claude/local/bin",
        home / ".local/bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        home / ".npm-global/bin",
        home / ".volta/bin",
    ]


def test_candidate_dirs_list_nvm_versions_newest_first(tmp_path: Path) -> None:
    home = tmp_path / "home"
    for version in ("v9.1.0", "v18.19.0", "v20.11.0"):
        (home / ".nvm/versions/node" / version / "bin").mkdir(parents=True)
    versions = [path.parent.name for path in claude_adapter.candidate_dirs(home)[6:]]
    assert versions == ["v20.11.0", "v18.19.0", "v9.1.0"]


def test_resolve_bin_finds_claude_off_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A double-clicked .app inherits a bare PATH, so the usual install
    locations have to be searched by hand."""
    home = tmp_path / "home"
    installed = _executable(home / ".claude/local/bin/claude")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert claude_adapter.resolve_bin("claude", home) == str(installed)


def test_resolve_bin_prefers_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _executable(home / ".claude/local/bin/claude")
    on_path = _executable(tmp_path / "bin" / "claude")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert claude_adapter.resolve_bin("claude", home) == str(on_path)


def test_resolve_bin_ignores_a_non_executable_file(
    tmp_path: Path, home_only_dirs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/claude").write_text("not executable\n")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert claude_adapter.resolve_bin("claude", home) is None


def test_search_path_appends_existing_candidate_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".volta/bin").mkdir(parents=True)
    monkeypatch.setenv("PATH", "/usr/bin")
    parts = claude_adapter.search_path(home).split(":")
    assert parts[0] == "/usr/bin"
    assert str(home / ".volta/bin") in parts
    assert str(home / ".local/bin") not in parts  # not created, so not offered


def test_detect_finds_claude_outside_path(
    taprivo_home: Path, tmp_path: Path, home_only_dirs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    installed = _executable(home / ".npm-global/bin/claude")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    runner = FakeClaude()
    ad = ClaudeAdapter(Config(), home=home, runner=runner)
    detected = ad.detect()
    assert detected.found and detected.path == str(installed)
    assert runner.calls[0][0] == str(installed)  # the runner gets the absolute path


def test_default_runner_reports_a_missing_binary_instead_of_raising() -> None:
    proc = claude_adapter.default_runner(["/nonexistent/claude", "--version"])
    assert proc.returncode != 0
    assert proc.stdout == ""
