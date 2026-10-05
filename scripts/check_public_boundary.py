"""Reject references that cross the public repository boundary."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import unquote_to_bytes

ROOT = Path.cwd().resolve()
_HEX_ESCAPE = re.compile(rb"\\x([0-9a-fA-F]{2})")
_UNICODE_ESCAPE = re.compile(rb"\\u([0-9a-fA-F]{4})")
_REPLACE_CALL = re.compile(
    rb"(?P<quote>['\"])(?P<value>[^'\"\\]{1,256})(?P=quote)"
    rb"\s*\.replace\(\s*(?P<old_quote>['\"])(?P<old>[^'\"\\]{1,32})(?P=old_quote)"
    rb"\s*,\s*(?P<new_quote>['\"])(?P<new>[^'\"\\]{0,32})(?P=new_quote)\s*\)"
)
_SEPARATORS = re.compile(rb"[^a-z0-9]+")
_MASK = 0xA5
_ENCODED_SIGNATURES = (
    (203, 192, 209, 199, 202, 221, 203, 200, 214),
    (203, 200, 214, 198, 201, 204),
    (203, 200, 214, 199, 196, 198, 206, 192, 203, 193),
    (203, 200, 214, 200, 198, 213),
    (215, 202, 202, 209, 203, 200, 214),
    (196, 213, 204, 213, 201, 208, 194, 204, 203, 214, 203, 200, 214),
    (203, 200, 214, 198, 201, 202, 208, 193),
    (213, 192, 215, 214, 202, 203, 196, 201, 198, 202, 203, 209, 192, 221, 209),
    (203, 200, 208, 201, 209, 204, 198, 201, 202, 208, 193, 198, 202, 203, 209, 192, 221, 209),
    (194, 204, 209, 203, 200, 208, 201, 209, 204, 198, 201, 202, 208, 193),
)
_FORBIDDEN = tuple(bytes(value ^ _MASK for value in encoded) for encoded in _ENCODED_SIGNATURES)


class BoundaryScanError(RuntimeError):
    """Raised when repository state cannot be inspected safely."""


@dataclass(frozen=True)
class Finding:
    source: str
    path: bytes
    signature: bytes


def _git(*args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=ROOT, check=False, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise BoundaryScanError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _decode_escapes(data: bytes) -> bytes:
    def hex_value(match: re.Match[bytes]) -> bytes:
        return bytes((int(match.group(1), 16),))

    def unicode_value(match: re.Match[bytes]) -> bytes:
        value = int(match.group(1), 16)
        return chr(value).encode("utf-8")

    decoded = _UNICODE_ESCAPE.sub(unicode_value, _HEX_ESCAPE.sub(hex_value, data))
    for _ in range(3):
        previous = decoded
        decoded = unquote_to_bytes(decoded)
        decoded = unescape(decoded.decode("utf-8", "replace")).encode("utf-8")
        decoded = _REPLACE_CALL.sub(
            lambda match: match.group("value").replace(match.group("old"), match.group("new")),
            decoded,
        )
        if decoded == previous:
            break
    return decoded


def _signature(data: bytes) -> bytes | None:
    normalized = _SEPARATORS.sub(b"", _decode_escapes(data).lower())
    return next((signature for signature in _FORBIDDEN if signature in normalized), None)


def _index_entries() -> list[tuple[bytes, bytes]]:
    output = _git("ls-files", "--stage", "-z")
    entries: list[tuple[bytes, bytes]] = []
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, separator, path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise BoundaryScanError("malformed git index entry")
        mode, object_id, stage = fields
        if stage != b"0":
            raise BoundaryScanError(f"unmerged index entry: {os.fsdecode(path)}")
        if mode not in {b"100644", b"100755", b"120000"}:
            raise BoundaryScanError(f"unsupported index mode {mode!r}: {os.fsdecode(path)}")
        entries.append((path, object_id))
    return entries


def _worktree_paths() -> list[bytes]:
    output = _git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return [path for path in output.split(b"\0") if path]


def scan() -> list[Finding]:
    findings: list[Finding] = []
    for path, object_id in _index_entries():
        data = _git("cat-file", "blob", object_id.decode("ascii"))
        if signature := _signature(path + b"\0" + data):
            findings.append(Finding("index", path, signature))

    root_bytes = os.fsencode(ROOT)
    for path in _worktree_paths():
        absolute = os.path.join(root_bytes, path)
        try:
            mode = os.lstat(absolute).st_mode
            if stat.S_ISLNK(mode):
                data = os.readlink(absolute)
                if isinstance(data, str):
                    data = os.fsencode(data)
            elif stat.S_ISREG(mode):
                with open(absolute, "rb") as handle:
                    data = handle.read()
            else:
                raise BoundaryScanError(f"unsupported worktree entry: {os.fsdecode(path)}")
        except OSError as exc:
            raise BoundaryScanError(f"cannot inspect {os.fsdecode(path)}: {exc}") from exc
        if signature := _signature(path + b"\0" + data):
            findings.append(Finding("worktree", path, signature))
    return findings


def main() -> int:
    try:
        findings = scan()
    except BoundaryScanError as exc:
        print(f"public-boundary: unable to complete scan: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(
            f"public-boundary: {finding.source}: {os.fsdecode(finding.path)} "
            f"contains prohibited signature {finding.signature.hex()}",
            file=sys.stderr,
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
