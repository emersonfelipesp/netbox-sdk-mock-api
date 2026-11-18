# AGENTS.md — netbox-sdk Agent Index

## Repository Context

This guide is part of the public `netbox-sdk` repository. Use the nearest scoped `CLAUDE.md` or `AGENTS.md` for the files you are changing.

---

## Quick Start

| What you need | Go here |
|---|---|
| Architecture overview | [CLAUDE.md](CLAUDE.md) |
| SDK package details | [netbox_sdk/CLAUDE.md](netbox_sdk/CLAUDE.md) |
| CLI package details | [netbox_cli/CLAUDE.md](netbox_cli/CLAUDE.md) |
| TUI package details | [netbox_tui/CLAUDE.md](netbox_tui/CLAUDE.md) |
| Docs build details | [docs/CLAUDE.md](docs/CLAUDE.md) |
| CI workflow details | [.github/CLAUDE.md](.github/CLAUDE.md) |

## CLAUDE.md Index

Read the nearest scoped guide for the code you are changing.

- [.github/CLAUDE.md](.github/CLAUDE.md)
- [CLAUDE.md](CLAUDE.md)
- [docs/CLAUDE.md](docs/CLAUDE.md)
- [netbox_cli/CLAUDE.md](netbox_cli/CLAUDE.md)
- [netbox_cli/reference/CLAUDE.md](netbox_cli/reference/CLAUDE.md)
- [netbox_sdk/CLAUDE.md](netbox_sdk/CLAUDE.md)
- [netbox_sdk/reference/CLAUDE.md](netbox_sdk/reference/CLAUDE.md)
- [netbox_tui/CLAUDE.md](netbox_tui/CLAUDE.md)
- [netbox_tui/themes/CLAUDE.md](netbox_tui/themes/CLAUDE.md)
- [reference/CLAUDE.md](reference/CLAUDE.md)
- [reference/textual/CLAUDE.md](reference/textual/CLAUDE.md)
- [tests/CLAUDE.md](tests/CLAUDE.md)
## Package Snapshot

```
netbox_sdk/   core install
netbox_cli/   optional cli extra
netbox_tui/   optional tui extra
```

Rules:
1. Read the local `CLAUDE.md` before editing in that area.
2. Preserve package boundaries: SDK independent, CLI lazy-loads TUI, TUI depends on SDK.
3. Use absolute imports only.
4. Run the owning package suite (`uv run pytest -m suite_sdk|suite_cli|suite_tui`) for package-local changes, and `uv run pytest` for shared/main/release validation paths.
5. When merging `main` into a version branch (e.g. `v0.0.7.post1`), **`main` wins on conflicts** — use `git checkout --theirs` for conflicted paths after `git merge origin/main` on that branch. See [CLAUDE.md § Release Process](CLAUDE.md#release-process).

## Dependency Security

Run the complete applicable quality workflows on main pushes and every pull
request. Feature pushes do not start duplicate lint, security or test runs.
Preserve every pull-request target, manual admission and all existing job
bodies, runner choices, assertions and release workflows.

The security quality and test quality summaries run after every original
job, including failures. Require successful scope selection, exact boolean
scope outputs, success from every selected native matrix aggregate, and
skips only for unselected jobs. On main pushes, the test summary also
requires the original full mock and live NetBox matrices. Keep these stable
summary checks separate from path-specific matrix check names.

Scheduled and manual data generation proposes a run-owned pull request for
only its original generated output against the exact selected source branch.
It never directly updates or merges that target branch. Require the GitHub
provider and a verified branch ref before generation or writeback. Metadata
retains its original main-only write guard; model graphs retain the selected
branch and never transplant feature-derived data onto main.
Keep the original generators, events and no-change exits. Native CI and the
platform owner approval and merge rules apply; unattended generation does
not imply unattended publication. Use only the existing repository token
with contents and pull-request write permissions, without dispatch, extra
credentials, auto-merge, bypasses or polling. Preserve partial owned proposals
on failure, and refuse changed bases, duplicate branches and unexpected paths.

Retain the aiohttp security floor of 3.14.4 and the resolved dnspython floor of
2.9.2 and OpenTelemetry API floor of 1.45.2. Resolve security updates through
uv, preserve compatible OpenTelemetry family requirements, and audit the
complete exported graph without suppressing advisories.

Keep both docs installation paths in `pyproject.toml` aligned, regenerate
`uv.lock` with `uv`, and run `tests/test_dependency_security.py`. Audit the
complete graph exported with
`uv export --frozen --all-extras --all-groups --no-hashes --no-emit-project`;
never hand-edit `uv.lock` or weaken a security floor to satisfy resolution.


## Native FastAPI telemetry

The mock application uses FastAPI 0.143.0 native telemetry. Public export is opt-in: do not inject a collector endpoint, service identity, or automatic configuration flag. Preserve explicit caller configuration, SDK and per-signal disable controls, provider ownership, and scoped privacy filtering. Do not add a second FastAPI or ASGI instrumentor.

Run the native telemetry regression modules with the committed dependency lock. These tests use fresh subprocesses and loopback collectors to verify actual export, caller ownership after application shutdown, and normal and exceptional nested request context restoration. Ordinary tests disable remote telemetry export. Preserve independent generated models, metadata, documentation, and security changes when integrating this source update.
