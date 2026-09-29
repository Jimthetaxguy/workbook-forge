# v1 export — model → editable Excel

**Branch:** `impl/v1-export`  
**Base:** `ea27eef58ec73f6cdd8db400e66d239a20054f74` (main after PR #1)  
**Checkout:** `/Users/jamespustorino/code/_worktrees/workbook-forge-v1-export`

## Scope
Write the bound calculation into an editable workbook people can open. Prove export, then Excel open/edit/save/reimport when Desktop evidence is available.

## Depends on
Depends on calc-binding’s cell contract (which cells hold which formulas/values). Intake’s structural field names should match.

## First concrete task
Export the bound scenario to xlsx via existing export_xlsx / agent export, reimport, and compare formulas + values + structure; record every mismatch.

## Do not
- Do not rewrite history on `main` or force-push.
- Do not modify Codex session files under `~/.codex`.
- Keep shelf split: Forge owns compute + OOXML; cell-store owns the sealed event log (join later via FORGE_EDGE).
