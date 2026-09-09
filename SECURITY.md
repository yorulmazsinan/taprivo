# Security policy

## Supported versions

Only the latest release on the `main` branch receives fixes while the project
is pre-1.0.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on this repository
("Security" tab → "Report a vulnerability"). Do not open a public issue for
security problems and do not include tokens or private paths in reports.

You can expect an acknowledgement within a few days. Fixes are published as
a new patch release with a changelog entry.

## Scope notes

Taprivo listens only on loopback and requires a per-user bearer token. It does
not claim isolation from malicious processes running as the same user; the
goal is to block browser-originated requests and accidental exposure.

During `taprivo setup claude`, the bearer token is passed to the `claude` CLI
as a command-line argument so it can be stored in Claude Code's user-private
configuration; on macOS, process arguments are visible to other processes
running as the same user.

Camera frames are processed in memory by the vision worker and rendered only
in the Camera window; they are never written to disk, included in logs, or
exposed over MCP. The hand-tracking dependency is pinned to a release without
usage logging, and a test scans the installed wheel for logging endpoints.
