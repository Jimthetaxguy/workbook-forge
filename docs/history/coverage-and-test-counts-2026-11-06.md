# Coverage and test counts, checkpoint by checkpoint (2026-11-06)

`CONTEXT.md` keeps only the current numbers. This note records where each
number came from, so the next correction can regenerate them instead of
copying a figure from an older document. Counts describe their commit only;
none of them is an Excel-compatibility claim.

| Commit | Date | Implemented functions | Detailed semantic specs | Source records | Fixture cases | Python tests | Rust tests | Node tests |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `32f49e2` (toolkit checkpoint) | 2026-09-28 | 115 | 85 | 110 | 1,371 | 923 | 88 | not built |
| `c84ec07` (proposed-spec base) | 2026-10-14 | 115 | 85 | 110 | 1,491 | not recorded here | not recorded here | not built |
| `580aacd` (gate; catalog counts still match `c506165`) | 2026-11-06 | 123 | 86 | 116 | 1,691 | 4,037 passed | 233 passed, 1 ignored | 15 passed |

How each column is derived, from a checkout at the named commit:

- Implemented functions: `len(json.load(open("catalog/formulas.json"))["functions"])`.
  The 521-entry inventory minus this count gives the catalog-only figure
  (398 at `580aacd`).
- Detailed semantic specs: `len(... ["semantic_specs"])` in the same file.
- Source records: `len(... ["sources"])` in the same file.
- Fixture cases: `wc -l fixtures/formula-cases.jsonl`.
- Test counts: the `tools/gate.sh` log. The 2026-11-06 figures come from the
  clean-tree gate run on the integration commit of PRs #93, #94, #95 and #97,
  whose tree is identical to `580aacd`: pytest reported `4037 passed`, the
  Rust suites summed to 233 passed with the `scheduler_benchmark` release
  benchmark ignored, and the `web/forge-engine` Node suite reported 15 passed.
  The 2026-09-28 figures are the ones `docs/toolkit-delivery.md` recorded for
  the merged checkpoint.

Why the older documents disagreed: the 115 / 85 / 110 / 1,371 figures were the
2026-09-28 checkpoint and stayed in `CONTEXT.md` unlabelled while `README.md`
and `docs/toolkit-delivery.md` were moved to the later 123 / 86 / 116 / 1,691
figures. The 2026-11-06 correction labelled the checkpoint and added the
current block. The catalog columns still match `main` `c506165`. The Python,
Rust and Node columns are the `580aacd` gate and have not been re-run.
