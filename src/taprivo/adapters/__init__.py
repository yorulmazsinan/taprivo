"""Agent adapters and the factories that build the shipped ones."""

from __future__ import annotations

from taprivo.adapters.claude import ClaudeAdapter
from taprivo.adapters.cursor import CursorAdapter
from taprivo.config import Config

__all__ = ["ClaudeAdapter", "CursorAdapter", "make_claude_adapter", "make_cursor_adapter"]


def make_claude_adapter(config: Config) -> ClaudeAdapter:
    return ClaudeAdapter(config)


def make_cursor_adapter(config: Config) -> CursorAdapter:
    return CursorAdapter(config)
