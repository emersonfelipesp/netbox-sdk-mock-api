# Segurança de dependências

O extra opcional `docs` e o grupo de dependências `docs` são caminhos de
instalação independentes. As restrições de `mkdocs-material` devem permanecer
semanticamente idênticas e incluir um limite inferior estável igual ou superior
a `9.7.7`.

Regenere o lock universal com `uv`; não edite `uv.lock` manualmente:

```bash
uv lock --upgrade-package mkdocs-material
uv sync --frozen --all-extras --all-groups
```

Em seguida, valide o guard de política, todos os caminhos de dependências
instaláveis, a suíte completa, a documentação estrita e o build do pacote:

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

`tests/test_dependency_security.py` rejeita requisitos ausentes, duplicados,
condicionais, baseados em URL, somente com exclusão, pré-lançamento ou divergentes.
Ele também verifica cada registro correspondente no lock, sua procedência do
registro e os hashes SHA-256 dos artefatos. O workflow de segurança de dependências
do Gitea repete essas validações no Python 3.12.
