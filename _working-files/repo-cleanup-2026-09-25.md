---
author: "claude-code/Claude"
created: "2026-09-25T11:25:00-04:00"
agent: "codex/Codex"
date: "2026-09-28T00:34:00-04:00"
type: cleanup-record
task: "Continue Workbook Forge release cleanup and consolidate the local checkpoint"
status: private-release-review
summary: "Completed and pushed the Workbook Forge name pass; the GitHub repository uses the workbook-forge slug and remains private. Staged a verified, main-only public candidate without internal work records or Git history."
next_steps:
  - "Review ~/code/_working-files/workbook-forge-public-candidate-2026-09-28/RELEASE-CANDIDATE.md and decide whether to seed a fresh public repository from its snapshot."
  - "Change repository visibility only as a separate, deliberate release action."
  - "Continue with the Excel Desktop differential oracle before expanding formula coverage."
remaining:
  - "Workbook spill projection, broad formula semantics, and Excel 365 coverage remain future evaluator work."
  - "Public visibility remains deferred; the current public-safety scan found local absolute paths in two working notes, and internal autoresearch/working-file history needs curation."
open_questions: []
workspace: "."
---
# Workbook Forge cleanup continuation — 2026-09-27

## Main-only public candidate — 2026-09-28 00:34 EDT

- Staged a 22-file candidate at `~/code/_working-files/workbook-forge-public-candidate-2026-09-28/`, from main commit `6f4d4470cef55e72909f2359877bd83b9ae1b352`.
- The snapshot includes source, tests, catalogs, fixtures, license, and behavior documentation. It excludes `.autoresearch/`, `_working-files/`, `CONTEXT.md`, `docs/run-history.md`, all non-main refs, and Git history. Its README no longer points to excluded files or the private checkpoint SHA.
- Validation: 22 files, 1,875,405 bytes; checksum manifest and tarball match; no broken local Markdown links; no email, recognized credential, absolute path, internal artifact reference, or old Formula Atlas branding matches in the candidate.
- Review record: `~/code/_working-files/workbook-forge-public-candidate-2026-09-28/RELEASE-CANDIDATE.md`. No new GitHub repository or visibility change was made; the existing repo remains private.

## Release-prep continuation — 2026-09-27 16:55 EDT

- GitHub now reports the canonical repository as `Jimthetaxguy/workbook-forge`; visibility is still private. The local `origin` was updated and fetched successfully. `main`, `HEAD`, and `origin/main` are aligned at `6dba619`.
- The read-only privacy scan checked tracked files and all reachable local/remote-tracking Git history. It found no email addresses or recognized GitHub, AWS, Google, Slack, or private-key credential patterns. It found absolute local paths in this note and `repo-health-analysis-2026-09-24.md`; the current files now use repository-relative wording, while older commits retain the historical paths.
- The five source repositories in `catalog/open_source_patterns.json` were checked against their upstream license evidence: Apache Commons Math, Apache OpenOffice, Apache POI, and ExcelFinancialFunctions list Apache-2.0; Formualizer v0.7.0 lists MIT and Apache-2.0. These match the project’s permissive, no-copyleft policy.
- `.autoresearch/` receipts and `_working-files/` reports are tracked in the current branch and history. A public-only export and a Git-history policy remain necessary before changing visibility.
- No visibility change was made.

## Current checkpoint

- Before this cleanup, local `main` and the live private `origin/main` both pointed to `e4ce4ba`; the live remote agent branch `agent/codex-formula-atlas` pointed to `8175b89`, an ancestor already included in `main`.
- Commit `15140a1` contains the Workbook Forge rebrand and metadata cleanup. It was pushed to private `origin/main` by fast-forward; the local `agent/codex-formula-atlas` branch was removed after confirming its commit was in `main`, while the remote branch reference remains.
- The Python distribution/import package and Rust crate use `workbook_forge`. Workbook Forge branding now covers the README, evaluator docstrings/errors, behavior and run-history docs, semantic catalog, source-pattern catalog, JSON Schemas, and shared fixtures. The source-pattern key is `workbook_forge_decision`; schema `$id` values use `workbook-forge.local`.
- `pyproject.toml` declares `license = "MIT"`, the SPDX string form. The package wheel reports `Name: workbook_forge` and `License-Expression: MIT`, carries six catalog JSON files, and excludes the test package.
- The checkout now lives at `~/code/workbook_forge`. At this cleanup checkpoint, the GitHub repository was `Jimthetaxguy/formula-atlas` and private; the later rename and origin update are recorded in the release-prep continuation above.

## Verification and duplicate-file audit

- Python: 190 tests passed; `compileall` passed.
- Rust: 31 tests passed; `fmt --check`, `check --locked`, and Clippy with warnings denied passed using the checkout-local Cargo target directory.
- Catalog schemas and the dependency license policy passed through the Python suite; `git diff --check` passed.
- Eight tracked Python/Rust source files plus one ignored `.remember/tmp/last-ndc.ts` tool scratch file were SHA-256 scanned: zero byte-identical source-file groups.
- The wheel build created eight ignored files under `build/` and `python/workbook_forge.egg-info/`, including three exact package-source copies. They were moved under `_archive-2026-09-27-L1/package-build/`; `MANIFEST.sha256.tsv` records their hashes. The active tree has no generated package-source copies.

## Next

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

## Rollback

```sh
cd ~/code/formula-atlas
sh _archive-2026-09-25-L1/ROLLBACK.sh
git restore README.md CONTEXT.md .gitignore
```

Rolling back also requires removing the new `docs/` folder and this note, or leaving them untracked.

## Deferred (not part of this cleanup)

- **Packaging:** `[tool.setuptools.packages.find]` has no `include`, so a wheel ships a stray top-level `tests` package. The fix is `include = ["formula_atlas*"]`.
- **Catalog JSON:** `catalog/formulas.json` `coverage.note` (13,723 chars) and `evaluator_completeness` (a 627-character hyphen-joined string) are changelogs stored inside data fields.
- **Tests:** several `test_catalog.py` tests assert the wording of disclaimers (for example "No direct Excel spot-checks") rather than structured fields.
- **Validation:** the Formula Laws validator design is awaiting James's approval.

## Activity

### 2026-09-25T23:57:00-04:00 — codex/Codex
- Changed: independently reran the documented Python and Rust gates after the cleanup and license-policy changes.
- Why/where: James asked to bring Formula Atlas to a good local stopping point; the checkout has no configured remote.
- Evidence: `python3.13 -m pytest -q` passed (190 tests), `python3.13 -m compileall -q python` passed, and Rust fmt/check/test/Clippy passed with `CARGO_TARGET_DIR="$PWD/target"` (31 tests). `git diff --check` is clean.
- Checkpoint: two local commits; nothing was pushed. The timestamped archive remains on disk and ignored by git for recovery.
- Next/remaining: the Excel Desktop differential oracle is next; deferred packaging and catalog cleanup items remain listed above.

### 2026-09-25T11:25:00-04:00 — claude-code/Claude
- Changed: archived the pre-git backup sprawl, restructured the docs word for word, added ignore rules, and wrote this record.
- Why/where: James asked for a cleanup of the current repo state while Codex is paused.
- Evidence: checksum manifest, sentence-level no-loss check, and `eval_command` exit 0.
- Next/remaining: the local commit awaits James's go-ahead, and the deferred items above remain open.
