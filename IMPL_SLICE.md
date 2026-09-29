# v1 agent-headless — ready-to-run agent path

**Branch:** `impl/v1-agent-headless`  
**Base:** `ea27eef58ec73f6cdd8db400e66d239a20054f74` (main after PR #1)  
**Checkout:** `/Users/jamespustorino/code/_worktrees/workbook-forge-v1-agent-headless`

## Scope
Headless install-and-run path for agents that consumes the structured model and existing agent ops (discover…export) without a UI.

## Depends on
Depends on intake report schema and preferably one working calc-binding receipt so the recipe is not empty. Export optional for dry-run inspect.

## First concrete task
Add one documented recipe: import fixture → intake report → run bound scenario → print receipt JSON; wire through existing JSON-lines agent ops where possible.

## Do not
- Do not rewrite history on `main` or force-push.
- Do not modify Codex session files under `~/.codex`.
- Keep shelf split: Forge owns compute + OOXML; cell-store owns the sealed event log (join later via FORGE_EDGE).
