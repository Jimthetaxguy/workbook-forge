# v1 calc-binding — model → Python/Rust verify

**Branch:** `impl/v1-calc-binding`  
**Base:** `ea27eef58ec73f6cdd8db400e66d239a20054f74` (main after PR #1)  
**Checkout:** `/Users/jamespustorino/code/_worktrees/workbook-forge-v1-calc-binding`

## Scope
Bind one existing calculation (prefer the operating-scenario revenue/profit example) to explicit worksheet cells. Run it through both Python and Rust backends and compare to Excel saved/cached results.

## Depends on
Needs a stable structured model from intake (same cell addresses and formula fields). Export not required for the first compare if cached values are present, but the binding contract must be export-ready.

## First concrete task
Pick the scenario cells, write a binding manifest (inputs/outputs), run both engines, and emit a pass/fail receipt of value diffs.

## Do not
- Do not rewrite history on `main` or force-push.
- Do not modify Codex session files under `~/.codex`.
- Keep shelf split: Forge owns compute + OOXML; cell-store owns the sealed event log (join later via FORGE_EDGE).

## Vision
Follow [docs/vision.md](docs/vision.md). Shared types and build rules there bind this slice to the others.
