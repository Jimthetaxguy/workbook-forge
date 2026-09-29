# v1 intake — Excel → structured model

**Branch:** `impl/v1-intake`  
**Base:** `ea27eef58ec73f6cdd8db400e66d239a20054f74` (main after PR #1)  
**Checkout:** `/Users/jamespustorino/code/_worktrees/workbook-forge-v1-intake`

## Scope
First vertical slice: read a workbook and emit a structured model (cells, formulas, cached values, layout, links) plus a compact overview. Readable Markdown preview is a scan layer; the cell map is source of record. Surface unsupported or incomplete extraction.

## Depends on
Independent of calc-binding and export for the first pass. Align field names with existing WorkbookModel / import_xlsx so later slices can consume the same JSON.

## First concrete task
Define the intake report schema (overview + cell map) and implement one CLI/agent op that imports a fixture xlsx and writes that report without mutating the source workbook.

## Do not
- Do not rewrite history on `main` or force-push.
- Do not modify Codex session files under `~/.codex`.
- Keep shelf split: Forge owns compute + OOXML; cell-store owns the sealed event log (join later via FORGE_EDGE).

## Status (2026-09-28)
Landed first concrete implementation:

- `python/workbook_forge/model.py` — SoT types: `Cell`, `Formula`, `Sheet`, `Workbook`
- `python/workbook_forge/intake.py` — `intake_workbook(path)` via bounded OOXML/`import_xlsx`; CLI `python -m workbook_forge.intake <file.xlsx>`
- `python/tests/test_intake.py` — fixture covers formula, cross-sheet ref, number format, empty cell

Later slices (calc-binding, export, agent-headless) should import `workbook_forge.model` directly.
