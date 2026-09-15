# Dependency security

The optional `docs` extra and the `docs` dependency group are independent
installation paths. Their `mkdocs-material` constraints must remain semantically
identical and must include a stable lower bound at or above `9.7.7`.

Regenerate the universal lock with `uv`; do not edit `uv.lock` by hand:

```bash
uv lock --upgrade-package mkdocs-material
uv sync --frozen --all-extras --all-groups
```

Then verify the policy guard, every installable dependency path, the full suite,
strict documentation, and the package build:

```bash
mkdir -p .tmp
uv run pytest tests/test_dependency_security.py
uv export --frozen --all-extras --all-groups --no-hashes --no-emit-project \
  --output-file .tmp/audit-requirements.txt
uvx --from pip-audit==2.10.1 pip-audit -r .tmp/audit-requirements.txt
uv run pytest
uv run mkdocs build --strict
uv build
```

`tests/test_dependency_security.py` rejects missing, duplicate, conditional,
URL-based, exclusion-only, prerelease, and divergent requirements. It also checks
every matching lock record, its registry provenance, and its SHA-256 archive hashes.
The Gitea dependency-security workflow repeats these checks on Python 3.12.
