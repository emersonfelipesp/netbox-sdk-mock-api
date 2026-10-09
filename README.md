# netbox-sdk

`netbox-sdk` is an SDK-first NetBox toolkit with terminal interfaces built on
one shared runtime:

- `netbox_cli` — Typer command-line interface
- `netbox_tui` — Textual terminal applications
- `netbox_sdk` — standalone REST API SDK shared by both

Published package names remain:

- `netbox-sdk`
- `netbox-console`

## Quick Start with the Demo Instance

Install:

```bash
pip install 'netbox-sdk[all]'
```

Authenticate against the public demo instance:

```bash
nbx demo init
```

Try a few commands:

```bash
nbx demo dcim devices list
nbx demo ipam prefixes list
nbx demo tui
nbx demo dev tui
```

## Install

Current release documented on the docs site matches **`docs/snippets/package-version.txt`** (aligned with `pyproject.toml`). For the latest PyPI build you can omit the pin; add `==<version>` to match that documentation snapshot.

Minimal SDK only:

```bash
pip install netbox-sdk
```

CLI:

```bash
pip install 'netbox-sdk[cli]'
```

TUI:

```bash
pip install 'netbox-sdk[tui]'
```

Everything:

```bash
pip install 'netbox-sdk[all]'
```

Pinned (same version as the docs site / `package-version.txt`):

```bash
pip install 'netbox-sdk[all]==0.0.7.post1'
```

With `uv` as a user tool:

```bash
uv tool install --force 'netbox-sdk[cli]'
```

Developer checkout:

```bash
git clone https://github.com/emersonfelipesp/netbox-sdk.git
cd netbox-sdk
uv sync --dev --extra cli --extra tui --extra demo
uv run nbx --help
```

## Common Commands

```bash
nbx init
nbx dcim devices list
nbx dcim devices get --id 1
nbx tui
nbx dev tui
nbx cli tui
nbx logs
```

## Architecture

- `netbox_sdk` owns config, auth, caching, schema parsing, request resolution, shared formatting, and demo helpers.
- `netbox_cli` owns the `nbx` command tree and lazy-loads `netbox_tui` where needed.
- `netbox_tui` owns all Textual apps, themes, widgets, and TCSS.

## Contributor Workflow

```bash
uv sync --dev --extra cli --extra tui --extra demo
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
uv run pre-commit run --all-files
uv run ty check netbox_sdk netbox_cli netbox_tui tests
uv run pytest
```

Dependency updates must keep both docs requirements in `pyproject.toml` aligned with
`uv.lock`. Validate the complete installable graph with:

```bash
mkdir -p .tmp
uv sync --frozen --all-extras --all-groups
uv run pytest tests/test_dependency_security.py
uv export --frozen --all-extras --all-groups --no-hashes --no-emit-project \
  --output-file .tmp/audit-requirements.txt
uvx --from pip-audit==2.10.1 pip-audit -r .tmp/audit-requirements.txt
```

See [Dependency security](docs/developer/dependency-security.md) for the update and
verification contract.

## Release Process

Use a single GitHub release title pattern for every release:

- `netbox-sdk vX.Y.Z`

Example:

```bash
gh release create v0.0.7.post1 \
  --title "netbox-sdk v0.0.7.post1"
```

When cutting a release, bump **`pyproject.toml`** and **`netbox_sdk.__version__`**, then keep docs in sync: **`docs/snippets/package-version.txt`**, **`mkdocs.yml`** → **`extra.package_version`**, and the version strings in **`docs/snippets/documented-release-*.md`** and **`docs/snippets/pip-pinned-*.txt`** / **`uv-pinned-cli.txt`**. **`uv lock`** must reflect the new version. **`tests/test_docs_alignment.py`** asserts snippet and MkDocs metadata match **`pyproject.toml`**.

## Native FastAPI telemetry

The standalone mock uses FastAPI standard 0.143.0 with native HTTP traces,
request metrics, validation and error logs, and WebSocket traces and logs.
Export has no default collector. Operators explicitly configure standard
`OTEL_EXPORTER_OTLP_ENDPOINT` or signal-specific endpoints; `OTEL_SDK_DISABLED`
and standard per-signal exporter controls remain available.

`create_mock_app(version=..., telemetry=...)` accepts native `TelemetryConfig`
without changing schema selection, route registration, branching, or reset
semantics. Explicit tracer, meter, and logger providers remain caller-owned.
Callers that already configure exporters should use `auto_configure=False`
and retain responsibility for flushing and shutdown. Do not wrap the app in
legacy FastAPI or ASGI instrumentation.

Selected SDK tracer and logger providers receive privacy processors before
native environment exporters attach. Concrete path and query values are
redacted; native exception messages and stack traces are omitted while error
classification, route templates, timing, and statuses remain. Earlier
caller-installed log exporters need equivalent caller-managed filtering and
processor ordering. Providers without SDK processor support require caller
sanitization. Native exporters do not capture request bodies or headers.

The committed lock includes the native telemetry dependencies. Ordinary tests
disable remote export; actual subprocess tests use loopback OTLP collectors to
verify all native signals, privacy, explicit/global provider ownership, repeated
lifespans, empty destinations, and disable controls. Run the locked complete
regression and configured lint, type, boundary, and documentation checks before
publishing. Source verification does not establish a deployed collector export.


## FastAPI 0.143.0 composition

The application and generated runtime lock pin FastAPI standard 0.143.0. Optional extras declare a minimum of >=0.142.2, but the application still requires the exact standard dependency pin. Native exporter auto-configuration is opt-in through FASTAPI_OTEL_AUTO_CONFIGURE=true or explicit telemetry auto_configure=True. Explicit dictionary values override the environment. Factories inject no NMS endpoint, service identity or auto-configuration value. Preserve caller provider ownership, SDK disable, privacy processors and lifespan cleanup. Qualify all shipping factory variants, including normal and error excluded nested-request context restoration, before delivery.

Under OTEL_SDK_DISABLED=true, each factory copies the caller configuration and disables native auto-configuration, traces, metrics and logs. Selected caller SDK providers retain scoped privacy processing and remain caller-owned; the application must not mutate the input dictionary or shut those providers down. Qualify this contract with pre-created providers and positive caller-owned signals after application lifespan exit.
