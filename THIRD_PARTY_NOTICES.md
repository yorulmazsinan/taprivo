# Third-party notices

Taprivo is licensed under Apache-2.0. It depends on the following projects,
each under its own license. Distributions of Taprivo must keep these notices.

| Project | License | Notes |
|---|---|---|
| PySide6 / Qt for Python | LGPL-3.0 (also commercial) | Dynamically linked; users may replace the Qt libraries. Packaged builds must ship the LGPL text and relinking instructions. |
| MCP Python SDK (`mcp`) | MIT | |
| Pydantic | MIT | |
| Typer, Click | MIT, BSD-3-Clause | |
| uvicorn, Starlette | BSD-3-Clause | |
| PyYAML | MIT | |
| httpx2 (via `mcp`) | BSD-3-Clause | |

Development-only tools (pytest, pytest-qt, hypothesis, Ruff, mypy) are not
distributed with Taprivo. Run `uv pip list` in the project environment for the
exact versions in use.
