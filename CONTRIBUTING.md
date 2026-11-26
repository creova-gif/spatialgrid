# Contributing to SpatialGrid

Thank you for your interest in contributing to **SpatialGrid**!

## Development Setup

SpatialGrid requires **Python 3.12+** and uses `uv` (or `pip`) for dependency management.

```bash
git clone https://github.com/justinmafie/spatialgrid.git
cd spatialgrid
uv venv
uv pip install -e ".[dev]"
```

Or with standard `pip`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Engineering Standards

All contributions must adhere to [AGENTS.md](AGENTS.md):

1. **Architecture Decision Records (ADRs):** Record non-trivial design choices in `docs/adr/` using `docs/adr/000-template.md`.
2. **Static Analysis & Formatting:**
   - `ruff check src tests`
   - `mypy src tests` (strict mode enabled in `pyproject.toml`)
3. **Deterministic Tests:** Every PR must include deterministic unit tests (`pytest -v`).
4. **Benchmarks:** Run `spatialgrid benchmark` before claiming performance or accuracy improvements.
5. **Security:** Never commit `.env` files, API keys, or PII/PHI fixtures.

## Verification Checklist

Run the full check suite before opening a pull request:

```bash
ruff check src tests
mypy src tests
pytest -v
spatialgrid benchmark
```
