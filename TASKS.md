# spatialgrid — Execution Checklist

## Wave 1: Day 1–5 Setup
- [x] Set up `index/`, `solver/`, `mcp_tools/`, and `benchmarks/` directories
- [x] Load OpenStreetMap/GeoJSON facility data into Shapely `STRtree` + H3 with exact distance + capacity filters
- [x] Expose 3 deterministic spatial tools (`nearest_facilities`, `coverage_gap`, `optimize_dispatch`) via MCP & CLI
- [x] Run 20-query benchmark proving 100% geometric accuracy vs. raw LLM guessing

## Wave 2: MVP & Eval Gate
- [x] Write ADR-001 in `docs/adr/`
- [x] Implement core pipeline + deterministic unit/eval tests
- [x] Verify CI workflow passes (`ruff`, `mypy --strict`, `pytest`, `spatialgrid benchmark`)

## Wave 3: Launch & Design Partner Validation
- [x] Publish clean `README.md` with reproducible benchmark numbers
- [ ] Run first external design-partner or open-source release
