# Python environment

Use **uv** with this project's `.venv` and the locked dependencies:

```bash
uv sync --locked
uv run plm-gui
uv run python -m unittest discover -s tests -v
```

Run Python commands through `uv run`. The default `gui` dependency group and optional `qt` extra
are declared in [pyproject.toml](pyproject.toml). Directory responsibilities,
build commands and current documentation are indexed in [README.md](README.md)
and [docs/README.md](docs/README.md). Keep version history according to
[AGENTS.md](AGENTS.md).
