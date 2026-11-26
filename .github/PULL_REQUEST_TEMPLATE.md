## Summary
Briefly describe the change and link any related issue or ADR.

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Benchmark / documentation / CI update
- [ ] Refactoring (no functional change)

## Verification Checklist
- [ ] `ruff check src tests` passes
- [ ] `mypy src tests` passes (`--strict` compliant)
- [ ] `pytest -v` passes with deterministic unit tests
- [ ] `spatialgrid benchmark` runs cleanly (if solver/index/benchmark logic changed)
- [ ] No `.env` files, API keys, or PII/PHI fixtures committed
- [ ] Architectural decisions recorded in `docs/adr/` (if applicable)
