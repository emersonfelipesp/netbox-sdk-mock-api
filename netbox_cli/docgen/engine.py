"""Capture engine — parallel CLI invocation with config management.

Single Responsibility: orchestrates CLI captures (serial or parallel).
Dependency Inversion: depends on model protocols, not concrete Pydantic classes.

Parallelism strategy:
    Uses ``ProcessPoolExecutor`` for true process isolation.  Each worker
    process gets its own Python interpreter and ``sys.stdout``/``sys.stderr``
    so that ``CliRunner``'s stream manipulation does not cause cross-thread
    interference.

    Concurrency is bounded by ``max_concurrency`` (default 4) to avoid
    overwhelming the NetBox demo instance.

Config lifecycle:
    1. Main process creates a temporary config root with placeholder credentials.
    2. Every worker and CLI subprocess receives that root explicitly.
    3. Main process removes the isolated root after capture, including on failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import TextIO

from netbox_cli.docgen.models import (
    DEFAULT_MAX_CONCURRENCY,
    CaptureArtifact,
    CaptureResult,
    CaptureSpec,
    build_slug,
)

_SUBPROCESS_TIMEOUT_SECONDS = float(os.environ.get("NBX_DOC_CAPTURE_SUBPROCESS_TIMEOUT", "15"))
_LOCAL_CLI_BOOTSTRAP = "import netbox_cli, sys; raise SystemExit(netbox_cli.main(sys.argv[1:]))"


def _local_cli_command(args: list[str]) -> list[str]:
    """Run the current checkout's CLI instead of whichever ``nbx`` is on PATH."""
    return [sys.executable, "-c", _LOCAL_CLI_BOOTSTRAP, *args]


def _isolated_subprocess_env(config_home: str) -> dict[str, str]:
    """Return an environment that cannot merge host credentials into captures."""
    env = dict(os.environ)
    for name in (
        "NETBOX_URL",
        "NETBOX_TOKEN_KEY",
        "NETBOX_TOKEN_SECRET",
        "NETBOX_SSL_VERIFY",
        "DEMO_USERNAME",
        "DEMO_PASSWORD",
    ):
        env.pop(name, None)
    env["XDG_CONFIG_HOME"] = config_home
    return env


# ── Top-level worker function (required for ProcessPoolExecutor) ─────────


def _worker_capture(
    spec_dict: dict,
    *,
    profile: str,
    markdown_output: bool,
    config_home: str,
    capture_config: dict[str, object],
) -> dict:
    """Execute a single capture in a child process.

    Returns a plain dict (not ``CaptureResult``) so that results are
    picklable across process boundaries.
    """
    import netbox_cli as cli_mod
    from netbox_cli import runtime as _rt
    from netbox_cli.docgen.format import convert_json_to_variants
    from netbox_cli.docgen.models import (
        inject_format_flag,
        supports_format_variants,
    )
    from netbox_sdk.config import Config

    cli_mod._RUNTIME_CONFIGS[profile] = Config.model_validate(capture_config)
    subprocess_env = _isolated_subprocess_env(config_home)

    _rt._get_index()

    argv: list[str] = list(spec_dict["argv"])
    argv_base: list[str] = list(argv)
    safe: bool = spec_dict["safe"]

    _MARKDOWN_ACTIONS = frozenset({"list", "get", "create", "update", "patch", "delete"})
    _FORMAT_FLAGS = frozenset({"--json", "--yaml", "--markdown"})

    if markdown_output:
        if "--help" not in argv and not any(f in argv for f in _FORMAT_FLAGS):
            opt_idx = next((i for i, t in enumerate(argv) if t.startswith("-")), len(argv))
            pos = argv[:opt_idx]
            if pos:
                if pos[0] == "call" and len(pos) >= 3:
                    argv = [*argv, "--markdown"]
                else:
                    body = pos[1:] if pos[0] == "demo" else pos
                    if len(body) >= 3 and body[-1] in _MARKDOWN_ACTIONS:
                        argv = [*argv, "--markdown"]

    def _invoke(args: list[str], catch: bool) -> tuple[int, str, float]:
        started = time.perf_counter()
        try:
            result = subprocess.run(
                _local_cli_command(args),
                capture_output=True,
                env=subprocess_env,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            )
            elapsed = time.perf_counter() - started
            out = result.stdout or ""
            err = result.stderr or ""
            if err.strip():
                out = f"{out}\n--- stderr ---\n{err}" if out.strip() else f"--- stderr ---\n{err}"
            return result.returncode, out, elapsed
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - started
            return 124, "", elapsed
        except Exception as e:
            elapsed = time.perf_counter() - started
            out = f"--- exception ---\n{type(e).__name__}: {e}"
            return 1, out, elapsed

    code, stdout, elapsed = _invoke(argv, catch=not safe)

    stdout_json = None
    stdout_yaml = None
    stdout_markdown = None

    if supports_format_variants(spec_dict["argv"]):
        json_argv = inject_format_flag(argv, "--json")
        _, json_stdout, _ = _invoke(json_argv, catch=True)
        variants = convert_json_to_variants(json_stdout)
        if variants is not None:
            stdout_json = variants.json_text
            stdout_yaml = variants.yaml_text
            stdout_markdown = variants.markdown_text

    return {
        "surface": spec_dict["surface"],
        "section": spec_dict["section"],
        "title": spec_dict["title"],
        "argv": argv,
        "argv_base": argv_base,
        "exit_code": code,
        "elapsed_seconds": round(elapsed, 3),
        "stdout_full": stdout,
        "truncated": False,
        "stdout_json": stdout_json,
        "stdout_yaml": stdout_yaml,
        "stdout_markdown": stdout_markdown,
    }


# ── Engine class ─────────────────────────────────────────────────────────────


class CaptureEngine:
    """Executes CLI captures in parallel (process isolation) or serial.

    Usage::

        engine = CaptureEngine(max_concurrency=4)
        results = engine.capture_all(specs, profile="demo")
        engine.write_artifacts(results, raw_dir)
    """

    def __init__(
        self,
        *,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_lines: int = 200,
        max_chars: int = 120_000,
        markdown_output: bool = True,
        log: TextIO | None = None,
    ) -> None:
        self._concurrency = max(1, max_concurrency)
        # Backward-compatible no-op knobs: capture output is never truncated.
        self._max_lines = max_lines
        self._max_chars = max_chars
        self._markdown_output = markdown_output
        self._log = log or sys.stderr

    # ── Public API ────────────────────────────────────────────────────────

    def capture_all(
        self,
        specs: list[CaptureSpec],
        *,
        profile: str,
    ) -> list[CaptureResult]:
        """Capture every spec, returning results in spec order.

        When *max_concurrency* > 1, specs are dispatched to a process pool
        for true parallelism.  Each worker is an isolated Python process
        with its own ``sys.stdout`` and ``CliRunner``.
        """
        with tempfile.TemporaryDirectory(prefix="nbx-docgen-config-") as config_home:
            capture_config = self._write_isolated_config(profile, Path(config_home))
            if self._concurrency <= 1 or len(specs) <= 1:
                return self._capture_serial(
                    specs,
                    config_home=config_home,
                )
            return self._capture_parallel(
                specs,
                profile=profile,
                config_home=config_home,
                capture_config=capture_config,
            )

    def write_artifacts(
        self,
        results: list[CaptureResult],
        raw_dir: Path,
    ) -> list[CaptureArtifact]:
        """Write one JSON artifact per result.  Returns the artifact list."""
        raw_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[CaptureArtifact] = []
        for result in results:
            slug = build_slug(result.surface, result.section, result.title)
            filename = f"{len(artifacts) + 1:03d}-{slug}.json"
            artifact = CaptureArtifact(result=result, filename=filename)
            (raw_dir / filename).write_text(
                json.dumps(result.to_dict(), indent=2),
                encoding="utf-8",
            )
            artifacts.append(artifact)
        return artifacts

    # ── Serial execution ──────────────────────────────────────────────────

    def _capture_serial(
        self,
        specs: list[CaptureSpec],
        *,
        config_home: str,
    ) -> list[CaptureResult]:
        from netbox_cli import runtime as _rt  # noqa: PLC0415

        # Pre-load schema.
        _rt._get_index()

        return [self._run_one_serial(spec, config_home=config_home) for spec in specs]

    def _run_one_serial(
        self,
        spec: CaptureSpec,
        *,
        config_home: str,
    ) -> CaptureResult:
        from netbox_cli.docgen.format import convert_json_to_variants  # noqa: PLC0415
        from netbox_cli.docgen.models import (  # noqa: PLC0415
            inject_format_flag,
            supports_format_variants,
        )
        from netbox_cli.docgen_capture import argv_with_markdown_output  # noqa: PLC0415

        argv_base = list(spec.argv)
        argv = argv_with_markdown_output(spec.argv, enabled=self._markdown_output)
        code, stdout, elapsed = self._invoke_cli(argv, safe=spec.safe, config_home=config_home)

        result = CaptureResult(
            surface=spec.surface,
            section=spec.section,
            title=spec.title,
            argv=argv,
            argv_base=argv_base,
            exit_code=code,
            elapsed_seconds=elapsed,
            stdout_full=stdout,
            truncated=False,
        )

        if supports_format_variants(spec.argv):
            json_argv = inject_format_flag(argv, "--json")
            _, json_stdout, _ = self._invoke_cli(json_argv, safe=True, config_home=config_home)
            variants = convert_json_to_variants(json_stdout)
            if variants is not None:
                result.stdout_json = variants.json_text
                result.stdout_yaml = variants.yaml_text
                result.stdout_markdown = variants.markdown_text

        return result

    def _invoke_cli(
        self, argv: list[str], *, safe: bool, config_home: str
    ) -> tuple[int, str, float]:
        started = time.perf_counter()
        try:
            result = subprocess.run(
                _local_cli_command(argv),
                capture_output=True,
                env=_isolated_subprocess_env(config_home),
                text=True,
                timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            )
            elapsed = time.perf_counter() - started
            out = result.stdout or ""
            err = result.stderr or ""
            if err.strip():
                out = f"{out}\n--- stderr ---\n{err}" if out.strip() else f"--- stderr ---\n{err}"
            return result.returncode, out, elapsed
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - started
            return 124, "", elapsed
        except Exception as e:
            elapsed = time.perf_counter() - started
            out = f"--- exception ---\n{type(e).__name__}: {e}"
            return 1, out, elapsed

    # ── Parallel execution (ProcessPoolExecutor) ──────────────────────────

    def _capture_parallel(
        self,
        specs: list[CaptureSpec],
        *,
        profile: str,
        config_home: str,
        capture_config: dict[str, object],
    ) -> list[CaptureResult]:
        """Run captures across isolated worker processes."""
        spec_dicts = [
            {
                "surface": s.surface,
                "section": s.section,
                "title": s.title,
                "argv": list(s.argv),
                "safe": s.safe,
            }
            for s in specs
        ]
        results_raw: dict[int, dict] = {}

        with ProcessPoolExecutor(max_workers=self._concurrency) as pool:
            futures = {
                pool.submit(
                    _worker_capture,
                    spec_dict,
                    profile=profile,
                    markdown_output=self._markdown_output,
                    config_home=config_home,
                    capture_config=capture_config,
                ): idx
                for idx, spec_dict in enumerate(spec_dicts)
            }
            for future in as_completed(futures):
                idx = futures[future]
                results_raw[idx] = future.result()

        return [
            CaptureResult(
                surface=d["surface"],
                section=d["section"],
                title=d["title"],
                argv=d["argv"],
                argv_base=d.get("argv_base", d["argv"]),
                exit_code=d["exit_code"],
                elapsed_seconds=d["elapsed_seconds"],
                stdout_full=d["stdout_full"],
                truncated=d["truncated"],
                stdout_json=d.get("stdout_json"),
                stdout_yaml=d.get("stdout_yaml"),
                stdout_markdown=d.get("stdout_markdown"),
            )
            for d in (results_raw[i] for i in range(len(specs)))
        ]

    @staticmethod
    def _write_isolated_config(profile: str, config_home: Path) -> dict[str, object]:
        """Write placeholder capture credentials under an isolated config root."""
        from netbox_sdk.config import DEMO_BASE_URL, Config, normalize_base_url  # noqa: PLC0415

        base_url = (
            DEMO_BASE_URL
            if profile == "demo"
            else normalize_base_url(
                os.environ.get("NETBOX_URL", "https://netbox.example.com").strip()
            )
        )
        capture_config = Config(
            base_url=base_url,
            token_key="docgen-placeholder",
            token_secret="placeholder",
            timeout=30.0,
        ).model_dump()
        config_dir = config_home / "netbox-sdk"
        config_dir.mkdir(parents=True, mode=0o700)
        config_path = config_dir / "config.json"
        config_path.write_text(
            json.dumps({"profiles": {profile: capture_config}}, indent=2),
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        return capture_config
