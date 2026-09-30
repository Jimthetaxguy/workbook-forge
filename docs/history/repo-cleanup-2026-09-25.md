---
author: "claude-code/Claude"
created: "2026-09-25T11:25:00-04:00"
agent: codex/agent_consumer
date: '2026-09-28T16:05:17-04:00'
type: cleanup-record
task: "Continue Workbook Forge release cleanup and consolidate the local checkpoint"
status: historical-record
summary: "Historical cleanup and release-preparation checkpoints from September 25–28, 2026. The naming pass and separately staged public candidate were verified at those checkpoints; current project state lives in CONTEXT.md."
next_steps:
  - "At the recorded checkpoint: review the separately staged, main-only public candidate and decide whether to seed a fresh public repository from it."
  - "Change repository visibility only as a separate, deliberate release action."
  - "Continue with the Excel Desktop differential oracle before expanding formula coverage."
remaining:
  - "Workbook spill projection, broad formula semantics, and Excel 365 coverage remain future evaluator work."
  - "At the recorded checkpoint, public visibility was deferred pending privacy and history review; the scan found local paths in two working notes."
open_questions: []
workspace: "."
---
# Workbook Forge cleanup continuation — 2026-09-27

This is a historical cleanup record. Repository visibility, package contents, next steps, and test results below describe their dated checkpoints. The 2026-09-28 documentation review clarified that scope and removed personal conversation references and machine-specific locations; it did not rerun those historical checks. See [CONTEXT.md](../../CONTEXT.md) for current project state. Ignored archive paths below identify local recovery evidence that is not shipped with the repository.

## Main-only public candidate — 2026-09-28 00:34 EDT

- Staged a 22-file candidate in a separate release staging directory, from main commit `6f4d4470cef55e72909f2359877bd83b9ae1b352`.
- The snapshot includes source, tests, catalogs, fixtures, license, and behavior documentation. It excludes `.autoresearch/`, `_working-files/`, `CONTEXT.md`, `docs/run-history.md`, all non-main refs, and Git history. Its README no longer points to excluded files or the private checkpoint SHA.
- Validation: 22 files, 1,875,405 bytes; checksum manifest and tarball match; no broken local Markdown links; no email, recognized credential, absolute path, internal artifact reference, or old Formula Atlas branding matches in the candidate.
- A local release-review record accompanied the candidate. No new GitHub repository or visibility change was made; the existing repo remained private at this checkpoint.

## Release-prep continuation — 2026-09-27 16:55 EDT

- GitHub now reports the canonical repository as `Jimthetaxguy/workbook-forge`; visibility is still private. The local `origin` was updated and fetched successfully. `main`, `HEAD`, and `origin/main` are aligned at `6dba619`.
- The read-only privacy scan checked tracked files and all reachable local/remote-tracking Git history. It found no email addresses or recognized GitHub, AWS, Google, Slack, or private-key credential patterns. It found absolute local paths in this note and `repo-health-analysis-2026-09-24.md`; the current files now use repository-relative wording, while older commits retain the historical paths.
- The five source repositories in `catalog/open_source_patterns.json` were checked against their upstream license evidence: Apache Commons Math, Apache OpenOffice, Apache POI, and ExcelFinancialFunctions list Apache-2.0; Formualizer v0.7.0 lists MIT and Apache-2.0. These match the project’s permissive, no-copyleft policy.
- `.autoresearch/` receipts and `_working-files/` reports are tracked in the current branch and history. A public-only export and a Git-history policy remain necessary before changing visibility.
- No visibility change was made.

## Cleanup checkpoint — 2026-09-27

- Before this cleanup, local `main` and the live private `origin/main` both pointed to `e4ce4ba`; the live remote agent branch `agent/codex-formula-atlas` pointed to `8175b89`, an ancestor already included in `main`.
- Commit `15140a1` contains the Workbook Forge rebrand and metadata cleanup. It was pushed to private `origin/main` by fast-forward; the local `agent/codex-formula-atlas` branch was removed after confirming its commit was in `main`, while the remote branch reference remains.
- The Python distribution/import package and Rust crate use `workbook_forge`. Workbook Forge branding now covers the README, evaluator docstrings/errors, behavior and run-history docs, semantic catalog, source-pattern catalog, JSON Schemas, and shared fixtures. The source-pattern key is `workbook_forge_decision`; schema `$id` values use `workbook-forge.local`.
- `pyproject.toml` declares `license = "MIT"`, the SPDX string form. The package wheel reports `Name: workbook_forge` and `License-Expression: MIT`, carries six catalog JSON files, and excludes the test package.
- The checkout directory was renamed to match the project. At this cleanup checkpoint, the GitHub repository was `Jimthetaxguy/formula-atlas` and private; the later rename and origin update are recorded in the release-prep continuation above.

## Verification and duplicate-file audit

- Python: 190 tests passed; `compileall` passed.
- Rust: 31 tests passed; `fmt --check`, `check --locked`, and Clippy with warnings denied passed using the checkout-local Cargo target directory.
- Catalog schemas and the dependency license policy passed through the Python suite; `git diff --check` passed.
- Eight tracked Python/Rust source files plus one ignored local review helper were SHA-256 scanned: zero byte-identical source-file groups.
- The wheel build created eight ignored files under `build/` and `python/workbook_forge.egg-info/`, including three exact package-source copies. They were moved under `_archive-2026-09-27-L1/package-build/`; `MANIFEST.sha256.tsv` records their hashes. The active tree has no generated package-source copies.

## Next steps recorded at that checkpoint

Keep the remote private until the public-release review and repository slug rename are complete. Continue the Excel Desktop differential oracle as the next evaluator milestone.

---
# Repo cleanup — 2026-09-25

## Starting state

Commit `42126ef` (Run 24) sat on `agent/codex-formula-atlas` with 26 tracked files and a clean diff. Around it were 462 untracked files. Most were hand-made backups from before git was in use: 22 `.autoresearch/_archive-*` folders, 133 files in `.autoresearch/backups/`, and 56 `*.bak-*` copies spread through the tree. There were also stale ignored build outputs, `build/` and `python/formula_atlas.egg-info/`. The egg-info still listed `tests` as a top-level package.

`README.md` had reached 30,311 characters (one line was 10,286 characters). `CONTEXT.md` had reached 35,927 characters with its run history out of order. Both said 1,366 fixtures; the actual count is 1,371.

## Queue and outcomes

| Item | Decision | Outcome |
|---|---|---|
| `.autoresearch/_archive-*` (22 dirs), `.autoresearch/backups/`, 56 `*.bak-*` files | Archive | Moved to `_archive-2026-09-25-L1/` with relative paths preserved |
| `build/`, `python/formula_atlas.egg-info/` (git-ignored, stale) | Archive | Moved after `git check-ignore` confirmed both are ignored |
| `_working-files/repo-health-evidence-2026-09-24/` (review harnesses) | Archive | Moved; the analysis report now points to the archived path |
| `.autoresearch/config.json`, `state.json` | Keep | Unchanged (loop state) |
| 19 untracked Jev receipts, `_working-files/repo-health-analysis-2026-09-24.md` | Track | Left in place for the commit (Codex already tracks the Run 21/22 receipts) |
| `rust/target/` (132 MB) and caches | Keep | Codex's isolated Cargo directory; already ignored |
| `.gitignore` | Edit | Added `_archive-*/`, `*.bak-*`, `.autoresearch/backups/` |
| `README.md`, `CONTEXT.md` | Restructure | History moved to `docs/run-history.md` and behavior notes to `docs/behavior-profiles.md`, word for word. README gained a Status table with current counts, and its verification command now matches `eval_command` (`CARGO_TARGET_DIR` inside the checkout) |

## Verification

- **Archive:** all 453 files were verified at their archived paths by SHA-256. No original path remains, and `sh -n ROLLBACK.sh` passes.
- **Documentation:** a sentence-by-sentence comparison against HEAD found 10 sentences not carried over word for word. All 10 were intentional:
  - two front-matter fields;
  - two obsolete headings;
  - the old verification command;
  - five stale count sentences, now replaced by the Status table.
- **Gate:** `.autoresearch/config.json` `eval_command` exits 0. Python passes 185 tests, Rust passes 31, and fmt, check and Clippy are clean. All three catalog schemas validate with 0 errors.
- **Size:**

  | File | Before | After |
  |---|---|---|
  | `README.md` | 30,311 chars | 8,712 chars |
  | `CONTEXT.md` | 35,927 chars | 7,796 chars |

## Historical rollback procedure

These commands describe recovery from the 2026-09-25 cleanup at that checkpoint. They are not rollback instructions for the current tree.

```sh
# From the repository root at the 2026-09-25 checkpoint
sh _archive-2026-09-25-L1/ROLLBACK.sh
git restore README.md CONTEXT.md .gitignore
```

At that checkpoint, rollback also required removing the newly added `docs/` folder and this note, or leaving them untracked.

## Deferred (not part of this cleanup)

- **Packaging:** `[tool.setuptools.packages.find]` has no `include`, so a wheel ships a stray top-level `tests` package. The fix is `include = ["formula_atlas*"]`.
- **Catalog JSON:** `catalog/formulas.json` `coverage.note` (13,723 chars) and `evaluator_completeness` (a 627-character hyphen-joined string) are changelogs stored inside data fields.
- **Tests:** several `test_catalog.py` tests assert the wording of disclaimers (for example "No direct Excel spot-checks") rather than structured fields.
- **Validation:** the Formula Laws validator design was awaiting approval.

## Activity

### 2026-09-25T23:57:00-04:00 — codex/Codex
- Changed: independently reran the documented Python and Rust gates after the cleanup and license-policy changes.
- Why/where: establish a verified local checkpoint for Formula Atlas; the checkout had no configured remote.
- Evidence: `python3.13 -m pytest -q` passed (190 tests), `python3.13 -m compileall -q python` passed, and Rust fmt/check/test/Clippy passed with `CARGO_TARGET_DIR="$PWD/target"` (31 tests). `git diff --check` is clean.
- Checkpoint: two local commits; nothing was pushed. The timestamped archive remains on disk and ignored by git for recovery.
- Next/remaining: the Excel Desktop differential oracle is next; deferred packaging and catalog cleanup items remain listed above.

### 2026-09-25T11:25:00-04:00 — claude-code/Claude
- Changed: archived the pre-git backup sprawl, restructured the docs word for word, added ignore rules, and wrote this record.
- Why/where: clean up the current repository state during a pause in implementation.
- Evidence: checksum manifest, sentence-level no-loss check, and `eval_command` exit 0.
- Next/remaining: approval for the local commit was still pending, and the deferred items above remained open.
