from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_public_boundary.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("check_public_boundary", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True)


def _scan(repo: Path):
    scanner = _load_scanner()
    token = b"qvz"
    scanner.ROOT = repo
    scanner._FORBIDDEN = (token,)
    return scanner.scan()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Boundary Test")
    (tmp_path / "safe.txt").write_text("public SDK\n", encoding="utf-8")
    _git(tmp_path, "add", "safe.txt")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


@pytest.mark.parametrize(
    "payload",
    [
        b"private qvz coupling",
        b"private q / v / z coupling",
        b"private q\\x76z coupling",
        b"private q&#118;z coupling",
        b"private q%76z coupling",
        b"""private "qxz".replace("x", "v") coupling""",
        b"private q\0v\0z coupling",
        b"private " + b"q" + b"vz coupling",
    ],
)
def test_rejects_embedded_split_escaped_and_nul_forms(repo: Path, payload: bytes) -> None:
    (repo / "candidate.bin").write_bytes(payload)
    findings = _scan(repo)
    assert any(finding.path == b"candidate.bin" for finding in findings)


def test_rejects_symlink_target(repo: Path) -> None:
    os.symlink("/private/" + "q" + "vz", repo / "candidate")
    findings = _scan(repo)
    assert any(finding.path == b"candidate" for finding in findings)


def test_rejects_staged_content_hidden_by_clean_worktree(repo: Path) -> None:
    candidate = repo / "candidate.txt"
    candidate.write_text("closed " + "q" + "vz backend", encoding="utf-8")
    _git(repo, "add", "candidate.txt")
    candidate.write_text("public SDK\n", encoding="utf-8")
    findings = _scan(repo)
    assert any(
        finding.source == "index" and finding.path == b"candidate.txt" for finding in findings
    )


def test_accepts_unrelated_acronym(repo: Path) -> None:
    (repo / "units.txt").write_text("Response time: 12 ms\n", encoding="utf-8")
    assert _scan(repo) == []
