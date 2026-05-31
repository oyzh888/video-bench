# CLAUDE.md

This file is the entry point Claude Code looks for in this repo.

**For all agent instructions — how to run the bench, push results,
contribute a new machine to the dashboard — see [AGENTS.md](./AGENTS.md).**

That doc is the single source of truth, kept in the format the wider
agent ecosystem (Codex, Cursor, etc.) expects. CLAUDE.md exists only to
point you there.

## Quick orientation

- `run.py` — orchestrator. `python3 run.py --label <name>` is the one command you'll run.
- `bench/` — individual suites: probe, single-clip, concurrent, scenarios, quality.
- `lib/` — shared helpers (common, capacity extrapolation, scoring).
- `results/*.json` — one file per machine-run. **This is the contract.** Push new ones here.
- `report.py` — turns `results/*.json` into `report.html`. Run by CI on every push to `results/`.
- `.github/workflows/pages.yml` — auto-deploys the dashboard.

## The full instructions

→ [AGENTS.md](./AGENTS.md) — read this before doing anything.
