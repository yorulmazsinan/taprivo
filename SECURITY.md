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
