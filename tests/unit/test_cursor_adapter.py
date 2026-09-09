from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from taprivo import paths
from taprivo.adapters.base import SetupError, SetupOptions, instructions_text
from taprivo.adapters.cursor import RULE_FRONTMATTER, CursorAdapter
from taprivo.config import Config

ENDPOINT = "http://127.0.0.1:32145/mcp"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def adapter(taprivo_home: Path, home: Path) -> CursorAdapter:
    return CursorAdapter(Config(), home=home, cursor_bin="/nonexistent/cursor")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "proj"
    path.mkdir()
    return path


def apply_all(adapter: CursorAdapter, options: SetupOptions) -> list[str]:
    return [action.apply() for action in adapter.plan_setup(options).actions]


def user_config(adapter: CursorAdapter) -> dict[str, object]:
    data = json.loads(adapter.user_config_path.read_text())
    assert isinstance(data, dict)
    return data


def test_detect_finds_cursor_directory(adapter: CursorAdapter, home: Path) -> None:
    assert adapter.detect().found is False
    (home / ".cursor").mkdir(parents=True)
    detected = adapter.detect()
    assert detected.found and detected.version is None
    assert detected.path == str(home / ".cursor")


def test_plan_lists_actions_and_writes_nothing(adapter: CursorAdapter, project: Path) -> None:
    plan = adapter.plan_setup(
        SetupOptions(project=True, install_instructions=True, project_dir=project)
    )
    assert [a.kind for a in plan.actions] == [
        "ensure_token",
        "merge_json",
        "write_file",
        "merge_json",
        "write_file",
    ]
    assert any("Settings > Rules" in note for note in plan.notes)
    assert not adapter.cursor_dir.exists()
    assert not (project / ".cursor").exists()
    assert not paths.token_path().exists()


def test_plan_without_instructions_notes_the_flag(adapter: CursorAdapter) -> None:
    plan = adapter.plan_setup(SetupOptions())
    assert [a.kind for a in plan.actions] == ["ensure_token", "merge_json"]
    assert any("--install-instructions" in note for note in plan.notes)


def test_user_merge_keeps_other_servers_and_locks_the_file(
    adapter: CursorAdapter, home: Path
) -> None:
    target = adapter.user_config_path
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}) + "\n")
    summaries = apply_all(adapter, SetupOptions(install_instructions=True))
    token = paths.token_path().read_text().strip()
    data = user_config(adapter)
    servers = data["mcpServers"]
    assert isinstance(servers, dict)
    assert servers["other"] == {"command": "x"}
    assert servers["taprivo"] == {
        "url": ENDPOINT,
        "headers": {"Authorization": f"Bearer {token}"},
    }
    assert target.read_text().endswith("}\n")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    backups = list(target.parent.glob("mcp.json.taprivo-backup-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())["mcpServers"] == {"other": {"command": "x"}}
    assert all(token not in summary for summary in summaries)
    assert (home / ".cursor" / "taprivo.md").read_text() == instructions_text()


def test_user_merge_creates_the_file_and_is_idempotent(adapter: CursorAdapter) -> None:
    apply_all(adapter, SetupOptions())
    target = adapter.user_config_path
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    summaries = apply_all(adapter, SetupOptions())
    assert any("already configured" in summary for summary in summaries)
    assert not list(target.parent.glob("mcp.json.taprivo-backup-*"))


def test_setup_summary_redacts_the_token(adapter: CursorAdapter) -> None:
    summaries = apply_all(adapter, SetupOptions())
    token = paths.token_path().read_text().strip()
    joined = "\n".join(summaries)
    assert token not in joined
    assert "[redacted]" in joined


def test_dry_run_descriptions_never_contain_the_token(adapter: CursorAdapter) -> None:
    paths.read_or_create_token()
    token = paths.token_path().read_text().strip()
    plan = adapter.plan_setup(SetupOptions(install_instructions=True))
    assert all(token not in action.description for action in plan.actions)
    assert all(token not in note for note in plan.notes)
    assert not adapter.user_config_path.exists()


def test_project_files_use_the_env_placeholder(adapter: CursorAdapter, project: Path) -> None:
    (project / ".cursor").mkdir()
    (project / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}})
    )
    summaries = apply_all(
        adapter, SetupOptions(project=True, install_instructions=True, project_dir=project)
    )
    config_path = project / ".cursor" / "mcp.json"
    data = json.loads(config_path.read_text())
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["taprivo"] == {
        "url": ENDPOINT,
        "headers": {"Authorization": "Bearer ${env:TAPRIVO_TOKEN}"},
    }
    token = paths.token_path().read_text().strip()
    assert token not in config_path.read_text()
    assert any("export TAPRIVO_TOKEN" in summary for summary in summaries)
    rule = project / ".cursor" / "rules" / "taprivo.mdc"
    assert rule.read_text() == RULE_FRONTMATTER + instructions_text()
    assert rule.read_text().startswith("---\ndescription: Taprivo Motion Energy budget\n")


def test_malformed_user_json_is_reported(adapter: CursorAdapter) -> None:
    adapter.user_config_path.parent.mkdir(parents=True)
    adapter.user_config_path.write_text("{not json")
    plan = adapter.plan_setup(SetupOptions())
    merge = next(a for a in plan.actions if a.kind == "merge_json")
    with pytest.raises(SetupError, match="not valid JSON"):
        merge.apply()


def test_remove_keeps_other_servers_and_deletes_only_taprivo_files(
    adapter: CursorAdapter, project: Path
) -> None:
    target = adapter.user_config_path
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    options = SetupOptions(project=True, install_instructions=True, project_dir=project)
    apply_all(adapter, options)
    plan = adapter.plan_remove(options)
    assert [a.kind for a in plan.actions] == [
        "edit_json",
        "delete_file",
        "edit_json",
        "delete_file",
    ]
    for action in plan.actions:
        action.apply()
    servers = user_config(adapter)["mcpServers"]
    assert isinstance(servers, dict)
    assert "taprivo" not in servers and servers["other"] == {"command": "x"}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not adapter.instructions_path.exists()
    project_servers = json.loads((project / ".cursor" / "mcp.json").read_text())["mcpServers"]
    assert "taprivo" not in project_servers
    assert not (project / ".cursor" / "rules" / "taprivo.mdc").exists()
    assert paths.token_path().exists()


def test_remove_without_anything_installed(adapter: CursorAdapter) -> None:
    plan = adapter.plan_remove(SetupOptions())
    assert plan.actions == []
    assert plan.notes == ["Nothing to remove."]


def test_verify_warns_when_cursor_is_absent(adapter: CursorAdapter) -> None:
    checks = adapter.verify()
    assert [(c.name, c.status) for c in checks] == [("cursor_registration", "warn")]
    assert checks[0].detail == "Cursor not registered"
    assert "taprivo setup cursor" in checks[0].hint


def test_verify_warns_about_missing_instructions(adapter: CursorAdapter, home: Path) -> None:
    (home / ".cursor").mkdir(parents=True)
    statuses = {c.name: c.status for c in adapter.verify()}
    assert statuses == {"cursor_registration": "warn", "cursor_instructions": "warn"}
    apply_all(adapter, SetupOptions())
    statuses = {c.name: c.status for c in adapter.verify()}
    assert statuses == {"cursor_registration": "ok", "cursor_instructions": "warn"}


def test_verify_all_ok_after_setup(adapter: CursorAdapter) -> None:
    apply_all(adapter, SetupOptions(install_instructions=True))
    assert {c.status for c in adapter.verify()} == {"ok"}


def test_verify_fails_on_a_different_url(adapter: CursorAdapter) -> None:
    apply_all(adapter, SetupOptions(install_instructions=True))
    data = user_config(adapter)
    servers = data["mcpServers"]
    assert isinstance(servers, dict)
    servers["taprivo"]["url"] = "http://127.0.0.1:9/mcp"
    adapter.user_config_path.write_text(json.dumps(data))
    checks = {c.name: c for c in adapter.verify()}
    assert checks["cursor_registration"].status == "fail"
    assert "http://127.0.0.1:9/mcp" in checks["cursor_registration"].detail


def test_verify_fails_on_unparsable_config(adapter: CursorAdapter) -> None:
    adapter.user_config_path.parent.mkdir(parents=True)
    adapter.user_config_path.write_text("{not json")
    checks = adapter.verify()
    assert [(c.name, c.status) for c in checks] == [("cursor_registration", "fail")]
