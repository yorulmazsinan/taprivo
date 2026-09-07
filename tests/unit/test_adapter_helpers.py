from pathlib import Path

import pytest

from taprivo.adapters.base import (
    backup,
    merge_mcp_server,
    redact,
    remove_block,
    remove_mcp_server,
    upsert_block,
    write_if_changed,
)

S, E = "<!-- t:start -->", "<!-- t:end -->"


def test_upsert_block_appends_once_and_updates_in_place() -> None:
    once = upsert_block("# Notes\n", "@x", S, E)
    assert once == f"# Notes\n\n{S}\n@x\n{E}\n"
    twice = upsert_block(once, "@x", S, E)
    assert twice == once
    updated = upsert_block(once, "@y", S, E)
    assert "@y" in updated and "@x" not in updated and updated.count(S) == 1


def test_upsert_block_on_empty_text() -> None:
    assert upsert_block("", "@x", S, E) == f"{S}\n@x\n{E}\n"


def test_remove_block_keeps_other_content() -> None:
    text = f"before\n\n{S}\n@x\n{E}\nafter\n"
    assert remove_block(text, S, E) == "before\n\nafter\n"
    assert remove_block("untouched\n", S, E) == "untouched\n"


@pytest.mark.parametrize("t", ["", "# Notes\n", "# Notes\n\n", "a\n\nb\n", "a\nb\n\n\n"])
def test_upsert_then_remove_block_round_trips(t: str) -> None:
    assert remove_block(upsert_block(t, "@x", S, E), S, E) == t


def test_upsert_then_remove_block_adds_missing_trailing_newline() -> None:
    assert remove_block(upsert_block("# Notes", "@x", S, E), S, E) == "# Notes\n"


def test_merge_and_remove_mcp_server_preserve_others() -> None:
    existing = {"mcpServers": {"other": {"type": "stdio", "command": "x"}}, "extra": 1}
    merged = merge_mcp_server(existing, "taprivo", {"type": "http", "url": "u"})
    assert merged["mcpServers"]["other"] == {"type": "stdio", "command": "x"}
    assert merged["mcpServers"]["taprivo"] == {"type": "http", "url": "u"}
    assert merged["extra"] == 1
    assert existing["mcpServers"].get("taprivo") is None
    removed = remove_mcp_server(merged, "taprivo")
    assert "taprivo" not in removed["mcpServers"] and "other" in removed["mcpServers"]


def test_write_if_changed_and_backup(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    assert write_if_changed(target, "a") is True
    assert write_if_changed(target, "a") is False
    copy = backup(target)
    assert copy.read_text() == "a" and copy.name.startswith("f.md.taprivo-backup-")


def test_redact() -> None:
    assert redact("Bearer secret123 failed", "secret123") == "Bearer [redacted] failed"
