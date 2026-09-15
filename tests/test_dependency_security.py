"""Regression guards for dependency-security policy."""

from __future__ import annotations

import tomllib
from pathlib import Path
from urllib.parse import urlparse

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import Specifier
from packaging.utils import canonicalize_name
from packaging.version import Version

pytestmark = pytest.mark.suite_sdk

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"
DEPENDENCY_WORKFLOW = ROOT / ".gitea" / "workflows" / "dependency-security.yml"
PACKAGE_NAME = "mkdocs-material"
SECURE_FLOOR = Version("9.7.7")
TRUSTED_ARCHIVES = {
    "mkdocs_material-9.7.7.tar.gz": "sha256:c0649c065b1b0512d60aad8c10f947f8e455284475239b364b610f2deb4d0855",
    "mkdocs_material-9.7.7-py3-none-any.whl": "sha256:8ea9bb1737a5b524a5f9dcf2e1b4ebda8274ae3008aa7845720a97083bef708f",
}


def _single_requirement(entries: list[str]) -> Requirement:
    matches = [
        Requirement(entry)
        for entry in entries
        if canonicalize_name(Requirement(entry).name) == PACKAGE_NAME
    ]
    assert len(matches) == 1, f"expected one {PACKAGE_NAME} requirement, found {len(matches)}"
    return matches[0]


def _assert_requirement_floor(requirement: Requirement) -> None:
    assert requirement.marker is None, f"{PACKAGE_NAME} must be unconditional"
    assert requirement.url is None, f"{PACKAGE_NAME} must resolve through the package index"
    assert not requirement.extras, f"{PACKAGE_NAME} must not depend on an extra"

    candidates = [_stable_bound_version(specifier) for specifier in requirement.specifier]
    stable_lower_bounds = [version for version in candidates if version is not None]

    assert stable_lower_bounds, f"{PACKAGE_NAME} needs an explicit stable lower bound or exact pin"
    assert max(stable_lower_bounds) >= SECURE_FLOOR, (
        f"{PACKAGE_NAME} must require at least {SECURE_FLOOR}"
    )


def _stable_bound_version(specifier: Specifier) -> Version | None:
    if specifier.operator not in {">=", "==", "~="}:
        return None
    if "*" in specifier.version:
        return None
    version = Version(specifier.version)
    if version.is_prerelease or version.is_devrelease or version.local is not None:
        return None
    return version


def _assert_aligned_specifiers(first: Requirement, second: Requirement) -> None:
    assert first.specifier == second.specifier, (
        f"{PACKAGE_NAME} constraints must match across both docs installation paths"
    )


def _assert_secure_lock(
    lock_data: dict[str, object], requirements: tuple[Requirement, ...]
) -> None:
    packages = lock_data.get("package")
    assert isinstance(packages, list), "uv.lock must contain package records"
    matches = [package for package in packages if _is_target_package(package)]
    assert matches, f"uv.lock must contain {PACKAGE_NAME}"

    for package in matches:
        _assert_secure_locked_package(package, requirements)


def _is_target_package(package: object) -> bool:
    if not isinstance(package, dict):
        return False
    return canonicalize_name(str(package.get("name", ""))) == PACKAGE_NAME


def _assert_secure_locked_package(
    package: dict[str, object], requirements: tuple[Requirement, ...]
) -> None:
    version = Version(str(package.get("version", "")))
    assert not version.is_prerelease, f"locked {PACKAGE_NAME} version must be stable: {version}"
    assert not version.is_devrelease, f"locked {PACKAGE_NAME} version must be stable: {version}"
    assert version.local is None, f"locked {PACKAGE_NAME} version must be public: {version}"
    assert version >= SECURE_FLOOR, f"locked {PACKAGE_NAME} {version} is vulnerable"
    assert all(version in requirement.specifier for requirement in requirements), (
        f"locked {PACKAGE_NAME} {version} violates a manifest constraint"
    )
    _assert_archive_provenance(package)


def _assert_archive_provenance(package: dict[str, object]) -> None:
    source = package.get("source")
    assert source == {"registry": "https://pypi.org/simple"}, (
        f"locked {PACKAGE_NAME} must use the canonical package index"
    )
    sdist = package.get("sdist")
    assert isinstance(sdist, dict), f"locked {PACKAGE_NAME} must include a source archive"
    _assert_trusted_archive(sdist, version=str(package["version"]), archive_type="sdist")
    wheels = package.get("wheels")
    assert isinstance(wheels, list), f"locked {PACKAGE_NAME} must include wheels"
    assert wheels, f"locked {PACKAGE_NAME} must include wheels"
    for wheel in wheels:
        _assert_trusted_archive(wheel, version=str(package["version"]), archive_type="wheel")


def _assert_trusted_archive(archive: object, *, version: str, archive_type: str) -> None:
    assert isinstance(archive, dict), f"locked {PACKAGE_NAME} {archive_type} must be a record"
    parsed = urlparse(str(archive.get("url", "")))
    assert parsed.scheme == "https", f"locked {PACKAGE_NAME} archives must use HTTPS"
    assert parsed.netloc == "files.pythonhosted.org", (
        f"locked {PACKAGE_NAME} archives must use canonical PyPI files"
    )
    filename = Path(parsed.path).name
    expected_prefix = f"mkdocs_material-{version}"
    assert filename.startswith(expected_prefix), (
        f"locked {PACKAGE_NAME} archive filename must match version {version}"
    )
    if archive_type == "sdist":
        assert filename == f"{expected_prefix}.tar.gz"
    else:
        assert filename.endswith(".whl")
    assert archive.get("hash") == TRUSTED_ARCHIVES.get(filename), (
        f"locked {PACKAGE_NAME} archive must match reviewed PyPI metadata: {filename}"
    )


def _locked_package(version: str) -> dict[str, object]:
    return {
        "name": PACKAGE_NAME,
        "version": version,
        "source": {"registry": "https://pypi.org/simple"},
        "sdist": {
            "url": "https://files.pythonhosted.org/packages/f1/cd/reviewed/mkdocs_material-9.7.7.tar.gz",
            "hash": TRUSTED_ARCHIVES["mkdocs_material-9.7.7.tar.gz"],
        },
        "wheels": [
            {
                "url": "https://files.pythonhosted.org/packages/ad/21/reviewed/mkdocs_material-9.7.7-py3-none-any.whl",
                "hash": TRUSTED_ARCHIVES["mkdocs_material-9.7.7-py3-none-any.whl"],
            }
        ],
    }


def test_mkdocs_material_is_secure_in_every_installation_path() -> None:
    manifest = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    optional = _single_requirement(manifest["project"]["optional-dependencies"]["docs"])
    dependency_group = _single_requirement(manifest["dependency-groups"]["docs"])

    _assert_requirement_floor(optional)
    _assert_requirement_floor(dependency_group)
    _assert_aligned_specifiers(optional, dependency_group)

    lock_data = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    _assert_secure_lock(lock_data, (optional, dependency_group))


@pytest.mark.parametrize(
    "raw_requirement",
    [
        PACKAGE_NAME,
        f"{PACKAGE_NAME}!=9.7.6",
        f"{PACKAGE_NAME}>=9.7.7rc1",
        f"{PACKAGE_NAME}==9.7.*",
        f"{PACKAGE_NAME}[recommended]>=9.7.7",
        f"{PACKAGE_NAME}>=9.7.7; python_version < '3.0'",
        f"{PACKAGE_NAME} @ https://example.invalid/mkdocs-material.whl",
    ],
)
def test_requirement_floor_rejects_bypass_forms(raw_requirement: str) -> None:
    with pytest.raises(AssertionError):
        _assert_requirement_floor(Requirement(raw_requirement))


@pytest.mark.parametrize(
    "raw_requirement",
    [
        f"{PACKAGE_NAME}>=9.7.7",
        f"{PACKAGE_NAME}>=9.8.0",
        f"{PACKAGE_NAME}==9.7.7",
        f"{PACKAGE_NAME}~=9.7.7",
        f"{PACKAGE_NAME}>=9.7.7,<10",
    ],
)
def test_requirement_floor_accepts_supported_stable_constraints(raw_requirement: str) -> None:
    _assert_requirement_floor(Requirement(raw_requirement))


def test_semantically_equivalent_specifier_order_is_aligned() -> None:
    first = Requirement(f"{PACKAGE_NAME}>=9.7.7,<10")
    second = Requirement(f"{PACKAGE_NAME}<10,>=9.7.7")
    _assert_aligned_specifiers(first, second)


def test_aligned_future_floor_is_accepted() -> None:
    requirement = Requirement(f"{PACKAGE_NAME}>=9.8.0")
    _assert_requirement_floor(requirement)
    _assert_aligned_specifiers(requirement, requirement)


def test_one_sided_future_floor_is_rejected() -> None:
    with pytest.raises(AssertionError):
        _assert_aligned_specifiers(
            Requirement(f"{PACKAGE_NAME}>=9.8.0"),
            Requirement(f"{PACKAGE_NAME}>=9.7.7"),
        )


def test_duplicate_manifest_requirement_is_rejected() -> None:
    with pytest.raises(AssertionError):
        _single_requirement([f"{PACKAGE_NAME}>=9.7.7", f"{PACKAGE_NAME}>=9.8.0"])


@pytest.mark.parametrize("locked_version", ["9.7.6", "9.7.7rc1", "9.8.0.dev1", "9.7.7+local"])
def test_every_locked_occurrence_must_be_secure_and_stable(locked_version: str) -> None:
    requirement = Requirement(f"{PACKAGE_NAME}>=9.7.7")
    lock_data = {"package": [_locked_package("9.7.7"), _locked_package(locked_version)]}
    with pytest.raises(AssertionError):
        _assert_secure_lock(lock_data, (requirement, requirement))


def test_truncated_archive_hash_is_rejected() -> None:
    requirement = Requirement(f"{PACKAGE_NAME}>=9.7.7")
    package = _locked_package("9.7.7")
    package["sdist"] = {"hash": "sha256:"}
    with pytest.raises(AssertionError):
        _assert_secure_lock({"package": [package]}, (requirement, requirement))


@pytest.mark.parametrize(
    ("url", "hash_value"),
    [
        ("", TRUSTED_ARCHIVES["mkdocs_material-9.7.7.tar.gz"]),
        (
            "file:///tmp/mkdocs_material-9.7.7.tar.gz",
            TRUSTED_ARCHIVES["mkdocs_material-9.7.7.tar.gz"],
        ),
        (
            "https://example.invalid/mkdocs_material-9.7.7.tar.gz",
            TRUSTED_ARCHIVES["mkdocs_material-9.7.7.tar.gz"],
        ),
        (
            "https://files.pythonhosted.org/packages/f1/cd/reviewed/other-9.7.7.tar.gz",
            TRUSTED_ARCHIVES["mkdocs_material-9.7.7.tar.gz"],
        ),
        (
            "https://files.pythonhosted.org/packages/f1/cd/reviewed/mkdocs_material-9.7.7.tar.gz",
            f"sha256:{'f' * 64}",
        ),
    ],
)
def test_untrusted_source_archive_metadata_is_rejected(url: str, hash_value: str) -> None:
    requirement = Requirement(f"{PACKAGE_NAME}>=9.7.7")
    package = _locked_package("9.7.7")
    package["sdist"] = {"url": url, "hash": hash_value}
    with pytest.raises(AssertionError):
        _assert_secure_lock({"package": [package]}, (requirement, requirement))


def test_ci_runtime_state_stays_outside_the_checkout() -> None:
    workflow = DEPENDENCY_WORKFLOW.read_text(encoding="utf-8")
    run_namespace = "${{ runner.temp }}/netbox-sdk-${{ gitea.run_id }}-${{ gitea.run_attempt }}"
    assert f"HOME: {run_namespace}/home" in workflow
    assert f"TMPDIR: {run_namespace}/tmp" in workflow
    assert f"UV_CACHE_DIR: {run_namespace}/uv-cache" in workflow
    assert "${{ runner.temp }}/home" not in workflow
    assert "${{ runner.temp }}/tmp" not in workflow
    assert "${{ runner.temp }}/uv-cache" not in workflow
    assert "${{ gitea.workspace }}/.uv-cache" not in workflow
