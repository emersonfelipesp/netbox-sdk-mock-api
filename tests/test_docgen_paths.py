"""Tests for docgen_capture path resolution."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

import netbox_cli
from netbox_cli import docgen_capture
from netbox_cli.docgen.engine import CaptureEngine, _local_cli_command
from netbox_cli.docgen.models import CaptureResult, CaptureSpec
from netbox_sdk.config import Config, config_path, save_profile_config

pytestmark = pytest.mark.suite_cli


class ArgvMarkdownOutputTests(unittest.TestCase):
    def test_disabled_returns_copy(self) -> None:
        argv = ["demo", "dcim", "devices", "list"]
        self.assertEqual(
            docgen_capture.argv_with_markdown_output(argv, enabled=False),
            argv,
        )

    def test_appends_for_demo_list(self) -> None:
        self.assertEqual(
            docgen_capture.argv_with_markdown_output(
                ["demo", "dcim", "devices", "list"], enabled=True
            ),
            ["demo", "dcim", "devices", "list", "--markdown"],
        )

    def test_appends_for_default_profile_list(self) -> None:
        self.assertEqual(
            docgen_capture.argv_with_markdown_output(["dcim", "devices", "list"], enabled=True),
            ["dcim", "devices", "list", "--markdown"],
        )

    def test_appends_before_trailing_options(self) -> None:
        self.assertEqual(
            docgen_capture.argv_with_markdown_output(
                ["demo", "dcim", "interfaces", "get", "--id", "1", "--trace"],
                enabled=True,
            ),
            ["demo", "dcim", "interfaces", "get", "--id", "1", "--trace", "--markdown"],
        )

    def test_skips_when_json_set(self) -> None:
        argv = ["call", "GET", "/api/dcim/sites/", "--json"]
        self.assertEqual(
            docgen_capture.argv_with_markdown_output(argv, enabled=True),
            argv,
        )

    def test_appends_for_call_get(self) -> None:
        self.assertEqual(
            docgen_capture.argv_with_markdown_output(["call", "GET", "/api/status/"], enabled=True),
            ["call", "GET", "/api/status/", "--markdown"],
        )

    def test_skips_help(self) -> None:
        argv = ["dcim", "devices", "list", "--help"]
        self.assertEqual(
            docgen_capture.argv_with_markdown_output(argv, enabled=True),
            argv,
        )

    def test_skips_groups_command(self) -> None:
        argv = ["groups"]
        self.assertEqual(
            docgen_capture.argv_with_markdown_output(argv, enabled=True),
            argv,
        )


class ResolveCapturePathsTests(unittest.TestCase):
    def test_defaults_under_repo_docs(self) -> None:
        out, raw = docgen_capture.resolve_capture_paths(None, None)
        self.assertTrue(out.name.endswith(".md"))
        self.assertEqual(raw.name, "raw")
        self.assertEqual(raw.parent, out.parent)

    def test_custom_output_infers_raw_sibling(self) -> None:
        out = Path("/tmp/nbx-cap/capture.md")
        o, r = docgen_capture.resolve_capture_paths(out, None)
        self.assertEqual(o, out)
        self.assertEqual(r, Path("/tmp/nbx-cap/raw"))

    def test_both_explicit(self) -> None:
        out = Path("/tmp/nbx-cap/capture.md")
        raw = Path("/tmp/nbx-cap/raw-custom")
        o, r = docgen_capture.resolve_capture_paths(out, raw)
        self.assertEqual(o, out)
        self.assertEqual(r, raw)


class LocalCliCommandTests(unittest.TestCase):
    def test_uses_current_python_instead_of_path_nbx(self) -> None:
        command = _local_cli_command(["docs", "generate-capture", "--help"])

        self.assertEqual(command[0], sys.executable)
        self.assertEqual(
            command[1:3],
            ["-c", "import netbox_cli, sys; raise SystemExit(netbox_cli.main(sys.argv[1:]))"],
        )
        self.assertEqual(command[3:], ["docs", "generate-capture", "--help"])


def test_committed_capture_metadata_omits_volatile_runtime_values(monkeypatch) -> None:
    monkeypatch.setenv("NETBOX_DEMO_URL", "https://private.invalid")
    monkeypatch.setenv("NBX_DOC_CAPTURE_TIMEOUT", "913.7")
    result = CaptureResult(
        surface="cli",
        section="Core",
        title="help",
        argv=["--help"],
        argv_base=["--help"],
        exit_code=0,
        elapsed_seconds=12.345,
        stdout_full="help\n",
    )

    meta = docgen_capture._build_meta(False)
    assert meta["generated_at"] == "reproducible-build"
    assert meta["netbox_url"] == "https://demo.netbox.dev"
    assert meta["token_configured"] is False
    assert meta["timeout"] == "30"
    assert result.to_dict()["elapsed_seconds"] == 0.0
    rendered = docgen_capture._render_markdown_capture(
        {**docgen_capture._build_meta(False), "netbox_url": "https://demo.netbox.dev"},
        [result],
    )
    assert "Wall time" not in rendered
    assert "12.345" not in rendered


def test_capture_uses_isolated_config_without_touching_user_config(
    tmp_path: Path, monkeypatch
) -> None:
    user_config_home = tmp_path / "user-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_config_home))
    monkeypatch.setenv("NBX_DOC_CAPTURE_TIMEOUT", "913.7")
    monkeypatch.setenv("NETBOX_TOKEN_KEY", "host-key")
    monkeypatch.setenv("NETBOX_TOKEN_SECRET", "host-secret")
    monkeypatch.setenv("DEMO_PASSWORD", "host-password")
    save_profile_config(
        "demo",
        Config(
            base_url="https://host-specific.invalid",
            token_key="host-key",
            token_secret="host-secret",
            timeout=913.7,
        ),
    )

    user_config_file = config_path()
    original_bytes = user_config_file.read_bytes()
    isolated_roots: list[Path] = []

    def fake_run(command, **kwargs):
        del command
        isolated_root = Path(kwargs["env"]["XDG_CONFIG_HOME"])
        assert "NETBOX_TOKEN_KEY" not in kwargs["env"]
        assert "NETBOX_TOKEN_SECRET" not in kwargs["env"]
        assert "DEMO_PASSWORD" not in kwargs["env"]
        isolated_roots.append(isolated_root)
        assert isolated_root != user_config_home
        payload = json.loads(
            (isolated_root / "netbox-sdk" / "config.json").read_text(encoding="utf-8")
        )
        capture_config = payload["profiles"]["demo"]
        assert capture_config["base_url"] == "https://demo.netbox.dev"
        assert capture_config["timeout"] == 30.0
        assert capture_config["token_key"] == "docgen-placeholder"
        assert capture_config["token_secret"] == "placeholder"
        return subprocess.CompletedProcess([], 0, stdout="help\n", stderr="")

    monkeypatch.setattr("netbox_cli.docgen.engine.subprocess.run", fake_run)
    engine = CaptureEngine(max_concurrency=1)
    engine.capture_all(
        [CaptureSpec(surface="cli", section="Core", title="help", argv=["--help"])],
        profile="demo",
    )

    assert user_config_file.read_bytes() == original_bytes
    assert len(isolated_roots) == 1
    assert not isolated_roots[0].exists()


@pytest.mark.parametrize("raise_failure", [False, True])
def test_isolated_capture_config_is_cleaned_up(
    tmp_path: Path, monkeypatch, raise_failure: bool
) -> None:
    created_roots: list[Path] = []
    original_writer = CaptureEngine._write_isolated_config

    def recording_writer(profile: str, config_home: Path) -> dict[str, object]:
        created_roots.append(config_home)
        return original_writer(profile, config_home)

    monkeypatch.setattr(CaptureEngine, "_write_isolated_config", staticmethod(recording_writer))
    engine = CaptureEngine(max_concurrency=1)
    if raise_failure:
        monkeypatch.setattr(
            engine,
            "_capture_serial",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("capture failed")),
        )
        with pytest.raises(RuntimeError, match="capture failed"):
            engine.capture_all([], profile="demo")
    else:
        engine.capture_all([], profile="demo")

    assert len(created_roots) == 1
    assert not created_roots[0].exists()


@pytest.mark.parametrize("raise_failure", [False, True])
def test_serial_capture_preserves_cached_user_profile(monkeypatch, raise_failure: bool) -> None:
    cached = Config(
        base_url="https://cached-user.invalid",
        token_key="cached-key",
        token_secret="cached-secret",
        timeout=71.0,
    )
    monkeypatch.setitem(netbox_cli._RUNTIME_CONFIGS, "demo", cached)
    engine = CaptureEngine(max_concurrency=1)

    if raise_failure:
        monkeypatch.setattr(
            engine,
            "_run_one_serial",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("capture failed")),
        )
        with pytest.raises(RuntimeError, match="capture failed"):
            engine.capture_all(
                [CaptureSpec(surface="cli", section="Core", title="help", argv=["--help"])],
                profile="demo",
            )
    else:
        monkeypatch.setattr(
            "netbox_cli.docgen.engine.subprocess.run",
            lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout="help\n", stderr=""),
        )
        engine.capture_all(
            [CaptureSpec(surface="cli", section="Core", title="help", argv=["--help"])],
            profile="demo",
        )

    assert netbox_cli._RUNTIME_CONFIGS["demo"] is cached
    assert netbox_cli._RUNTIME_CONFIGS["demo"] == cached


if __name__ == "__main__":
    unittest.main()
