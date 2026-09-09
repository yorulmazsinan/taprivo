"""Statically enforces the `vision/` layering rule from CONTRIBUTING.md: it
never imports Qt or `taprivo.mcp`, and `mediapipe` is only ever imported
lazily inside `vision/tracker.py`. Parses source with `ast` -- it never
imports mediapipe or PySide6 itself, so it stays dependency-free and fast
even on machines without either installed."""

from __future__ import annotations

import ast
from pathlib import Path

VISION_DIR = Path(__file__).resolve().parents[2] / "src" / "taprivo" / "vision"

FORBIDDEN_PREFIXES = ("PySide6", "taprivo.ui", "taprivo.mcp")


def _vision_modules() -> list[Path]:
    modules = sorted(VISION_DIR.glob("*.py"))
    assert modules, f"no modules found under {VISION_DIR}"
    return modules


def _imported_module_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    # ImportFrom: relative imports (`from . import x`) have `module=None`.
    return [node.module] if node.module else []


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Whether `node` is lexically inside a function (or method) body."""
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return True
        current = parents.get(current)
    return False


def test_vision_modules_never_import_qt_or_mcp() -> None:
    violations: list[str] = []
    for path in _vision_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for name in _imported_module_names(node):
                if name.startswith(FORBIDDEN_PREFIXES):
                    rel = path.relative_to(VISION_DIR.parents[2])
                    violations.append(f"{rel}: imports {name!r}")
    assert not violations, "vision/ must not import Qt or taprivo.mcp:\n" + "\n".join(violations)


def test_mediapipe_imported_only_in_tracker_inside_a_function_body() -> None:
    violations: list[str] = []
    for path in _vision_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = _build_parent_map(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for name in _imported_module_names(node):
                if name.split(".")[0] != "mediapipe":
                    continue
                if path.name != "tracker.py":
                    violations.append(f"{path.name}: mediapipe imported outside tracker.py")
                elif not _enclosing_function(node, parents):
                    violations.append(
                        f"{path.name}: mediapipe imported at module/class level, not lazily "
                        "inside a function body"
                    )
    assert not violations, "\n".join(violations)
    # Sanity check the check itself: tracker.py must actually import mediapipe
    # somewhere, or the assertions above would pass vacuously.
    tracker_src = (VISION_DIR / "tracker.py").read_text(encoding="utf-8")
    assert "import mediapipe" in tracker_src
