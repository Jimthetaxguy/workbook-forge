# Cell versions and cell preconditions

The command envelope (`CommandSession::apply_and_calculate`, the browser
`applyAndCalculate`) has always refused a command when *anything* in the
workbook changed since the client's `base_revision`. With several writers on
one workbook (a person and background agents, or two tabs), that refuses
edits that touch nothing the other writer changed. This page describes the
finer-grained check: a version per cell, and per-cell preconditions on the
envelope. The whole-workbook check stays the default.

## Cell version

A cell's version is **the workbook revision of the last applied edit that
wrote the cell**. Every path that writes cells counts: `set_cells`, `undo`,
`redo` and the legacy `applyEdits` (`applyEditsResultJson`). A write is any
change to the cell's input: a value, a formula, a style, or removing an
inserted cell again (an undone insertion).

- **Recalculation never bumps a version.** A cell whose *value* changes
  because a precedent changed, or because it calls `TODAY()`, `NOW()`,
  `RAND()` or `RANDBETWEEN()`, keeps the version of its last write. The
  version tracks the input (literal or formula text), not the calculated
  value.
- A cell no edit has written since the workbook opened has the **opening
  revision** (`0` for a workbook opened from bytes or created blank). That
  includes cells the workbook does not hold.
- The index lives in the Rust toolkit session (`toolkit::Session`, field
  `cell_revisions`) beside the model and is updated inside `apply` under the
  same lock that assigns revisions, so a lookup is O(1) and always agrees
  with the revision. Replay rebuilds it exactly, because replay reapplies the
  same logged inputs at the same revisions.

Where versions appear:

| Where | Field | Notes |
|---|---|---|
| `snapshot()` cells (TypeScript) | `cellRevision` | Present only for cells written since the workbook opened. |
| `applyAndCalculate` / patch `changed[]` | `cell_revision` | On every changed cell; a recalculated-only cell reports its earlier version. |
| WASM `BrowserWorkbook.cellRevisionsJson()` | `{"Sheet": {"A1": "3"}}` | Written cells only. |
| `replay` result (Rust) | `cell_revisions` | Same shape; the TypeScript replay snapshot carries `cellRevision`. |

Versions are decimal strings, like revisions.

## Preconditions

```json
{
  "v": 1, "command_id": "…", "client_id": "…", "client_seq": "4",
  "idempotency_key": null,
  "actor": {"kind": "human", "id": "alice", "on_behalf_of": null},
  "base_revision": "12",
  "mode": "cells",
  "preconditions": [{"sheet": "Sheet1", "address": "C7", "base_version": "9"}],
  "op": {"type": "set_cells", "edits": [{"sheet": "Sheet1", "address": "C7", "value": 42}]}
}
```

A precondition says "refuse unless this cell is unchanged since
`base_version`". The check is `version(cell) <= base_version`. A valid
`base_version` is the cell's version as last read, or any snapshot revision
the client took after that cell's last write. Any cell may be named, not only
an edited one, so a client can also guard a cell it read to compute an edit.

| Mode | Applies when | Refused when |
|---|---|---|
| `strict` (default) | `base_revision` equals the workbook revision **and** every precondition holds | the workbook moved at all (`revision_conflict`, no `stale_cells`), or a precondition fails |
| `cells` | every precondition holds, whatever else changed since `base_revision` | a precondition cell was written after its `base_version` |
| `rebase` | never | always: `unsupported_mode` |

`mode: "cells"` with no preconditions is a blind write. This is how a
last-write-wins *client policy* is expressed (ingested or agent columns): the
engine stays the one sequencer and does not need to know about the policy.

A refused precondition answers with `revision_conflict`, the existing code,
and lists every failing cell:

```json
{"code": "revision_conflict",
 "message": "1 precondition cell(s) changed since their base_version",
 "expected_revision": "12", "current_revision": "15",
 "stale_cells": [{"sheet": "Sheet1", "address": "C7", "base_version": "9",
                  "cell_revision": "14", "overwritten_by": "ev_0021"}]}
```

`overwritten_by` is the intent event that reached that revision in this
session's log, or `null` when the write did not go through a logged path.
Nothing is applied: a command is still all or nothing.

Other refusals, all applying nothing:

- `invalid_revision`: a `base_version` that is not a decimal string, or that
  is ahead of the workbook.
- `invalid_request` (not consumed for dedupe): an unknown field (the draft
  `{cell, unchanged_since}` shape is not accepted), a cell that does not
  resolve, two preconditions on one cell, more than 10,000 preconditions, or
  a mode other than `strict`, `cells` or `rebase`.
- `revision_conflict`: a `cells`-mode `base_revision` ahead of the workbook.
- `unsupported_precondition`: preconditions on `undo` or `redo`. Those run
  under their own cell rule (`undo_conflict` / `redo_conflict`).

The log records the mode and the preconditions in the intent's `base`:
`{"mode": "cells", "baseRevision": "12", "preconditions": [...]}`. Replay
does not re-check them; it reapplies the recorded inputs.

Atomicity: the envelope path reads the workbook revision and the precondition
versions under one session lock, then applies with a compare-and-swap at that
revision. A write that slips in through a path outside the envelope between
the two is refused as an ordinary `revision_conflict`, never applied over.

## Gaps

- **Python.** The Python toolkit and canonical model have no command
  envelope, event log or cell versions; `Session.apply(expected_revision=...)`
  stays whole-workbook. The native bridge (`native/src/lib.rs`) does not
  expose `CommandSession` either. Cell versions are Rust and browser only.
- **Not yet:** `on_precondition_failure: "skip"` (apply the eligible cells,
  report the rest as skipped), a row version (the adapter can take the
  maximum over a row's cells), a durable log (versions live as long as the
  session), and the agent protocol (`docs/agent-protocol.md`), which is
  unchanged.
- The Univer adapter (`web/univer-spike`) still sends `mode: "strict"` with
  no preconditions.
