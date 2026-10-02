---
author: "claude-code/Claude"
created: "2026-09-24T19:22:00-04:00"
agent: codex/agent_consumer
date: '2026-09-28T16:05:17-04:00'
type: analysis
task: "In-depth health analysis of Formula Atlas: what is going well and what is not (read-only review)"
status: historical-review
summary: "Python and Rust agree on all fixtures, but both give non-Excel answers on everyday semantics and have crash or hang paths. The loop never ran the plan's Excel check. Runs 21–22 fixed 3 of 21 disagreement classes, added 4, and pinned new non-Excel SORT behavior. Only 37% of the new fixtures match LibreOffice exactly."
next_steps:
  - "Confirm whether to pause new-family runs and allow local-only commits (plan step 6)."
  - "Run acceptance gates with a checkout-local CARGO_TARGET_DIR; the shared target directory mixed build output from different checkouts."
  - "Build the Excel cached-value oracle (plan step 4) before changing core semantics; Run 21's calculate_cells_to could compare against cached values."
remaining:
  - "The first findings were measured on an 18:19 EDT snapshot; the progress check used a 21:00 snapshot taken while Runs 21–22 were still unaccepted."
  - "Many preserved harness files hard-code scratchpad paths; parameterize them before reuse."
open_questions:
  - "Was the omitted Excel comparison blocked by sandbox automation limits, or by another cause?"
model: "claude-opus-5-5"
workspace: "."
---
# Formula Atlas: repo health analysis (2026-09-24)

This report describes the 2026-09-24 review snapshots, when the project was named Formula Atlas. Its results and recommendations were not rerun for the 2026-09-28 documentation review. Personal conversation references and machine-specific locations were removed; original technical findings, author, creation time, and checkpoint history remain. See [CONTEXT.md](../../CONTEXT.md) for current project state. The ignored evidence archive below is local recovery material and is not shipped with the repository.

## Progress check after Runs 21–22 (snapshot taken 21:00 EDT; both runs unaccepted)

- **What changed.**
  - Run 21 added `Workbook.calculate_cells_to`, a 32,767-UTF-16-unit cap on text results, and one shared number-to-text routine.
  - Run 22 added SORT and UNIQUE.
  - Fixtures went from 1,286 to 1,362 (SORT 23, UNIQUE 17, CONCAT 17, operators 17, other 2). Specs went from 84 to 85 and sources from 107 to 110.
  - `state.json` still ends at Run 20. Nothing is committed.
- **Gate, rebuilt in an isolated target dir.**
  - Python: 166 passed. Formatting and all three schemas also pass.
  - Rust: 25 passed and 1 failed. The failing test asserts SORT is unsupported (`lib.rs:6878`).
  - Clippy fails on `collapsible_if` at `lib.rs:737`.
- **New environment finding.** The review host configured one Cargo `target-dir` for multiple checkouts. Copies of this crate in different folders overwrote each other's artifacts there. A `cargo test` run from the frozen snapshot executed a binary built from the live tree (the panic was at `lib.rs:6921`, which exists only in the live file). Acceptance gates should use a checkout-local `CARGO_TARGET_DIR`. This review's earlier un-isolated gate runs also wrote to that shared directory.
- **Parity delta** (seed 20260924, run on the same corpus):

  | Part | Before | After |
  |---|---|---|
  | Mutants | 97.21% | 97.26% |
  | Random | 95.38% | 95.84% |
  | Disagreements | 913 | 852 (65 fixed, 4 new) |

  - Of the 21 root-cause classes, 3 are fixed, 3 changed and 15 are unchanged.
  - Two of the three fixes settled on answers that differ from Excel. `SUBSTITUTE("abc","","-")` is now #VALUE! in both engines (Excel: "abc"), and number-to-text now gives "1e-09"-style output in both.
  - The 4 new classes:
    - A Rust comparison with an error on the right-hand side now changes which error a formula returns.
    - Rust's ISERROR misses #CALC!, now also through UNIQUE.
    - UNIQUE resolves competing argument errors in a different order in each engine.
    - `ROWS(SORT(...))`: Python says unsupported, Rust returns #VALUE!.
- **Regression.** `=1E-05&""` now returns `1e-05` in both engines. Rust previously returned `0.00001`, which matches Excel. The routine chose shortest round-trip text instead of Excel's 15-significant-digit General format.
- **SORT and UNIQUE.**
  - Python and Rust agree on 97.87% of 3,000 generated cases, and on all 1,245 array results.
  - Both return #VALUE! for sort keys that contain blanks, booleans or mixed types, e.g. sorting sales rows when one sales cell is empty. An error in the key fails the whole sort.
  - UNIQUE treats "Å" and "å" as different values.
  - Fixtures pin these behaviors: 53 of the 76 new fixtures (70%) are "-profile" cases.
- **LibreOffice on the 76 new fixtures:** 36.8% agree exactly and 59.2% agree counting error classes. The whole original corpus scored 66.6% and 79.4%. The largest groups of disagreements are profile strictness (11 cases) and number rendering (10 cases).
- **Unchanged from the analysis below.**
  - Python still matches Excel on only 2 of the 23 headline checks.
  - Python still raises `RecursionError` at 1,000 terms, and the wildcard match takes 2.8 s on 300 characters.
  - Rust still aborts with a stack overflow at 300 nested calls (debug build), reads cells in O(n²), resolves duplicate keys nondeterministically, returns 1.23 for `ROUND(A5:A6,0)`, fails XLOOKUP on blanks, and returns 4 for `LEN(#N/A)`.
- **Fixed.** CONCAT error passthrough, the "-0" text output, and memory blowups from nested SUBSTITUTE (the text cap now catches them).

This is an isolated contribution under the working-files protocol, section 4. I did not edit
`CONTEXT.md`, `README.md`, the catalogs, the fixtures or the engines. Codex was actively running
Run 20 in this checkout during the review, and its coordinating writer can integrate what is useful.

## Scope and method

- **Snapshot.** Taken about 18:19 EDT and copied to a scratch directory. Every run and probe used the
  copy, never the live tree. SHA-256 prefixes: `python/formula_atlas/__init__.py` 9c353770,
  `rust/src/lib.rs` d752fce3, `fixtures/formula-cases.jsonl` bfc89eab, `catalog/formulas.json` 4513eb46.
- **Project gates on the snapshot:**
  - `pytest`: 139 passed.
  - `compileall`: OK.
  - `cargo check`: OK.
  - `cargo test`: 25 passed.
  - `cargo fmt --check`: **failed** (one diff).
  - `cargo clippy -D warnings`: **failed** (`let_and_return` near `lib.rs:908`).
  - All three JSON schemas validate with 0 errors (jsonschema Draft 2020-12 with format checking). Schema validation is not part of `eval_command`.
- **Independent checks:** four parallel reviewers, plus my own reproduction of their headline claims.
  1. Python engine review.
  2. Rust engine review, with isolated-process robustness probes and 4.8M fuzzed formulas.
  3. Python-vs-Rust differential fuzz: 24,864 generated cases.
  4. LibreOffice 26.8.0.3 run headless over all 1,286 fixtures, as an independent but imperfect oracle.

  Excel was not launched.
- ✔ marks results I reproduced myself; everything else comes from the reviewers' evidence
  (see [Evidence index](#evidence-index)).

## Bottom line

The skeleton is good and its labels are honest: it keeps documented behavior separate from local
choices, marks unsupported features explicitly, has no runtime dependencies, and its numeric
kernels are strong. The loop has been optimizing the wrong thing, though.

- **Agreement is not correctness.** Both engines pass all 1,286 fixtures because the same agent wrote
  both engines and every expected value. Yet both give non-Excel answers on everyday operations:
  blank-cell tests, type ordering, number-to-text conversion and 15-digit comparison.
- **Parity holds only on the fixtures.** The engines agree on 96.5% of fuzzed formulas.
- **Robustness is weak.** Rust can abort its whole process on a few hundred nested calls, and Python
  can hang on a wildcard criterion.
- **The plan was not followed.** Its explicit "compare selected cases with installed Excel" step ran
  0 times in 20 runs, and the loop spent most runs on long-tail financial and combinatoric functions.

## Going well

- **Dual engines against a language-neutral corpus.**
  - 113 functions are implemented in both Python and Rust, with zero runtime dependencies.
  - All 1,286 shared fixtures pass in both engines ✔.
  - Catalog arity matches the Python arity table for all 90 fixed-arity functions ✔. Every source reference resolves ✔.
- **Epistemic discipline.** Documented behavior is kept apart from "Formula Atlas profile" choices, unsupported syntax
  returns typed errors, and MIT/Apache-only provenance is recorded for each outside reference.
- **Rust memory safety.**
  - No panics across 4.8M fuzzed formulas. There is no `unsafe` code, all 27 unwrap/expect sites are guarded upstream, and text is indexed by character rather than byte, so Unicode can't split a character.
  - Range caps are enforced: `=SUM(A1:XFD1048576)` is rejected in about 0 ms ✔.
- **Numerical engineering in the kernels.**
  - `ExactNatural` computes exact integers and rounds once, ties-to-even.
  - VDB, coupon and workday logic is closed-form or O(log n).
  - All 21 extreme financial probes (1e308, 1e-308, a 2^53 life) finish in under 1 ms.
- **Per-run reviews catch real defects.** They found parity bugs in most runs and wrong expected values in Runs 6, 18 and 20.
- **Workbook adapter basics.** It blocks path traversal and does not resolve external XML entities (XXE). Saves are atomic, go to a new
  output file, and reject macros.

## What is not going well (ranked)

### 1. Both engines share non-Excel answers on everyday formulas (the corpus cannot see this)

| Formula | Formula Atlas (Python and Rust) | Excel |
|---|---|---|
| `=IF(A1="","empty","filled")`, A1 blank | `filled` ✔ | `empty` |
| `=A1=""`, A1 blank | FALSE ✔ | TRUE |
| `=TRUE>1` · `="a">1` | FALSE · #VALUE! ✔ | TRUE · TRUE (numbers < text < logicals) |
| `=TRUE=1` | TRUE ✔ | FALSE |
| `=0.1+0.2=0.3` | FALSE ✔ | TRUE |
| `=1/3&""` | `0.3333333333333333` ✔ | `0.333333333333333` (15 significant digits) |
| `=ROUND(1.005,2)` | 1.0 ✔ | 1.01 |
| `=LEN("😀")` | 1 ✔ | 2 (UTF-16 code units) |
| `=INDEX(A1:C1,2)` | #REF! ✔ | the value of B1 |
| `=1E308*10` | inf ✔ | #NUM! |

In Excel's order of types, numbers sort below text and text sorts below logicals.

### 2. Parity only holds on the hand-written corpus

| Corpus | Cases | Python and Rust agree |
|---|---|---|
| Hand-written fixtures | 1,286 | 100% |
| Mutated fixtures | 12,864 | 97.2% |
| Random compositions | 12,000 | 95.4% |

The 913 disagreements come from 21 root causes, at least 9 of them in everyday formulas. Neither engine
is consistently closer to Excel.

| Bug | Engine | Repro | Result |
|---|---|---|---|
| A range argument shifts later arguments by position | Rust | `=ROUND(A5:A6,0)` with A5=1.23456, A6=2 | 1.23 ✔ |
| | Rust | `=LEFT(B5:B6)` | "hel" ✔ |
| XLOOKUP/XMATCH fail on the first blank or mixed-type cell | Rust | `=XLOOKUP(2,A1:A4,B1:B4)` with A1 blank | #VALUE! ✔ (Python: "two") |
| LEN/LEFT/CONCAT turn errors into text | Rust | `=LEN(A1)` with A1=#N/A | 4 |
| Whole numbers stored as floats are rejected | Python | `=LEFT("abcdef",4/2)`, `=DATE(2024,6/2,1)` | #VALUE! ✔ |
| INDEX/VLOOKUP/MATCH return errors from cells they never select | Python | — | error |
| Approximate VLOOKUP/MATCH (the default mode) mishandles blanks and case | both | — | wrong |
| COUNTIF mixed-type criteria diverge | both | — | engines differ |
| AND/OR short-circuit and blank semantics diverge | both | — | engines differ |
| Empty SUM prints as "-0" | Rust | `=""&SUM(A1)` | "-0" ✔ |
| A range used as TEXTJOIN's delimiter prints the internal `_Range(...)` object | Python | `=TEXTJOIN(A1:A2,TRUE,"x","y","z")` | leaked text ✔ |
| ISERROR misses #CALC! | Rust | `=ISERROR(FILTER(1,0))` | Err(Calc) ✔ |

### 3. Robustness against untrusted formulas and workbooks

| Engine | Problem | Evidence |
|---|---|---|
| Rust | Unbounded recursion **aborts the process**; `catch_unwind` cannot recover it | Aborts at 300 nested `ABS(` in a debug build on the 8 MiB main thread ✔; 75 levels on a 2 MiB thread; 12,460 `+1` terms in release on 8 MiB. Parser at 372, eval at 533, exact-integer path at 1459 |
| Rust | Memory blowups end in an allocation abort | 15 nested `SUBSTITUTE(x,"a","aaaa")` (352 bytes) abort; the 100k-cell cap applies per range, not per formula (a 2.8 KB formula peaks at 2.15 GB) |
| Rust | Every cell read scans the whole cell map | O(n²): 1k cells 0.02 s, 4k cells 0.19 s ✔; 100k cells 158 s against 0.13 s in Python |
| Rust | Duplicate keys resolve nondeterministically | `{a1:1, A1:2}` → `=A1` returns 1 in 104 runs and 2 in 96 of 200 ✔ |
| Rust | SEARCH is O(n²) | 50k characters: 23.9 s |
| Python | `RecursionError` escapes `evaluate()` | 1,000-term chains and 200 nested parentheses raise ✔ (Excel allows 8,192-character formulas) |
| Python | Wildcard criteria backtrack catastrophically (ReDoS) | `=COUNTIF(A1,"*a*a*a*b")` takes 2.3 s at 300 characters and 11.8 s at 450 ✔ |
| Python | Numbers are not held to Excel's 64-bit float range | `=2^1024` returns a 309-digit integer ✔; a shared fixture locks this in (`COMBIN(2^1024-2^1024+7,1)` = 7) |
| Python | The broad `except` masks bugs as #VALUE! | `=SUM(2^1024,-1E+300)` → #VALUE! "int too large to convert to float" |
| Python | `workbook.py` trusts the uncompressed sizes an archive declares | A 2,587-byte zip peaks at 2,079 MB of memory; a UTF-16 sheet slips past the DTD guard |

### 4. Plan versus reality

| Plan item | Status |
|---|---|
| Independent engines on a shared corpus; explicit unsupported results; `.xlsx` read/patch to a new file | ✅ Done |
| "Do not commit or push" | ✅ Followed. 0 commits. The loop improvised versioning with 22 archive directories and 56 `.bak` files (13 MB, at least 4 naming schemes) |
| **Step 4: "compare selected cases with installed Excel" in every loop** | ❌ Ran 0 of 20 times. The docs mention "spot-check" 43 times; Excel.app is installed on this Mac |
| **Step 5 family order:** lookup → logical → text → dates → math → statistics → financial | ❌ Inverted (see the table below) |
| Tokenizer, AST, registry and dependency-tracking layers | ⚠️ The logical split exists, but each engine is a single file (3,659 and 6,457 lines). There is no function registry: Python uses an arity dict plus a 587-line if-chain, Rust a 533-line match. Dependency tracking is deferred |
| Per-function metadata (argument shape, availability, platform, volatility, description, examples) | ⚠️ Only 115 of 521 entries have arity and status, and 84 have specs. Availability is "unknown" for all 521. There is no refresh tooling |
| Syntax constructs: names, `@`, union/intersection, LET/LAMBDA, spill, structured references | ⚠️ Names, `@` and union/intersection are absent; the rest are catalogued only. Several statuses are stale, e.g. comparisons still say "evaluated" |
| Study Formualizer, the openpyxl tokenizer, CellRune and rxls | ⚠️ 1 of 4. openpyxl, CellRune and rxls appear nowhere in the repo |
| Use Jev to flag categories and scope gaps | ⚠️ Jev grades each run's wording instead ("supports narrower claim"). It never flagged the statistics or lookup gap |

Coverage by category:

| Category | Inventory | Implemented | Share |
|---|---|---|---|
| Date and time | 25 | 20 | 80% |
| Financial | 55 | 21 | 38% |
| Math and trig | 80 | 20 | 25% |
| Lookup and reference | 37 | 6 | 16% |
| Statistical | 111 | 14 | 13% |
| Engineering, Database, Compatibility, Cube, Web | 119 | 0 | 0% |

### 5. Effort went to the long tail; the core is thin

- **Everyday functions have very few shared cases, and 31 implemented functions have no semantic spec.**

  | Function | Shared fixtures |
  |---|---|
  | SUM | 5 |
  | VLOOKUP, MATCH | 3 each |
  | AVERAGE, COUNT, INDEX | 2 each |
  | LEFT, LEN, SUMIFS, ABS, TRIM, UPPER | 1 each |

  Functions without a spec include SUM, AVERAGE, COUNT, MAX, MIN, VLOOKUP, XLOOKUP, INDEX, MATCH, LEFT/MID/RIGHT and LEN.
- **The long tail has deep coverage instead.**

  | Function | Shared fixtures |
  |---|---|
  | COMBIN | 52 |
  | VDB | 43 |
  | NETWORKDAYS.INTL | 41 |
  | AMORDEGRC (deprecated) | 38 |
- **Common functions are missing.** 50 of 51 I checked are not implemented:
  - Statistics: MEDIAN, STDEV.S, LARGE, SMALL, RANK.EQ.
  - Lookup: CHOOSE, ROW, COLUMN, UNIQUE, SORT.
  - Text, dates and aggregation: TEXT, VALUE, TODAY, NOW, SUMPRODUCT.
  - Math and financial: SQRT, NPV, IRR, XIRR, RATE.
  - Modern formula features: LET, LAMBDA.

  The research brief explicitly said to skip "coupon-date math"; Run 6 implemented all six COUP* functions.

### 6. The loop's metric is saturated

- Every run from 1 to 19 scored 6/6 and was accepted, so the best run is still Run 1.
- The plateau rule (threshold 10) can never fire, so the loop runs to `max_runs: 30`.
- The rubric rewards internal consistency (parity, schema, provenance). It has no term for Excel
  agreement or real-world value, so each run added a neighboring family from Microsoft's function index.

### 7. Tests and documentation

- **The fixture test is one loop,** so the first mismatch hides the rest; 3 corrupted cases report as "1 failed".
- **The tolerance is `abs_tol=1e-12`.**
- **46% of fixture IDs carry "profile"** (the local-choice label), and 546 cases have neither a source reference nor a note.
- **`test_catalog.py` asserts on prose text,** e.g. `"No direct Excel spot-checks" in spec[...]`. That makes the tests
  preserve the disclaimers and fail as soon as someone adds Excel verification.
- **README.md** is 24.6k characters, with a single 10,197-character paragraph.
- **CONTEXT.md** (29.4k characters):
  - It states 10 different fixture counts and 10 function counts.
  - Its sections run Runs 14, 15, 19, 18, 17, 16, then slices "fourth" through "twentieth", then Run 20.
  - Two numbering schemes collide: "twentieth slice" is CUMIPMT, while "Run 20" is FILTER.
  - It says 1,271 fixtures; the actual count is 1,286.
- **In `catalog/formulas.json`,** `coverage.note` is a 12,262-character changelog string, and
  `evaluator_completeness` is a hyphen-joined string (584 characters) that grows every run.
- **Docs claim acceptance while the gate is red.** CONTEXT's "Current State" describes Run 20 as having cleared its gates while fmt and clippy are failing.

### 8. Fixture expectations LibreOffice disputes (probable fixture errors; confirm in Excel)

LibreOffice matched 856 of 1,286 fixtures exactly (66.6%). Counting cases where both sides return an
error but LibreOffice's code has no Excel equivalent, it reached 1,021 (79.4%).

| Fixture class | Agreement (exact + error-class) |
|---|---|
| Documented | 94.3% |
| Other | 83.3% |
| Profile | 71.6% |

LibreOffice is not Excel: it is one day off for dates before March 1900, supports a wider date range
and has its own AMOR* implementation. The cases below are probable fixture errors on the evidence, and
none of them carries a source reference:

| Fixture | Formula | Fixture expects | Probable Excel |
|---|---|---|---|
| `excel-serial-zero-day` | `=DAY(0)` | 31 | 0 (Excel shows serial 0 as 1/0/1900) |
| `mod-empty-text-as-zero` | `=MOD("",2)` | 0 | #VALUE! |
| `days360-us-month-end-start-and-end` | `=DAYS360(DATE(2023,1,31),DATE(2023,2,28))` | 30 | 28 (likely; the Days360 reference and Apache POI apply the end-date rule only on the 31st) |
| `days360-us-end-month-roll-forward` | `=DAYS360(DATE(2023,1,15),DATE(2023,2,28))` | 46 | 43 (likely) |
| `if-true-explicitly-omitted-value-profile` | `=IF(TRUE,)` | #VALUE! | 0 |
| `weekday-omitted-selector-default` | `=WEEKDAY(DATE(2008,2,14),)` | 5 | #NUM! (likely; return type 0) |
| `ipmt-error-before-numeric-validation` | `=IPMT(0.1,1E308*10,3,100,#N/A)` | #N/A | #NUM! (likely; this also contradicts the project's own leftmost-error profile) |

Another gap is by design: the fixtures pin #VALUE! for a range passed to a single-value function
(e.g. `HOUR(A1:A2)`), where Excel 365 spills an array. That is a large fidelity gap for a
Microsoft 365 target.

### 9. Smaller issues

- **Packaging.** `top_level.txt` lists `tests`, so a wheel install would drop a stray top-level `tests` package into site-packages.
- **`catalog.py`.** After `pip install --target` or `--prefix` it raises `FileNotFoundError`, and lookups return shallow copies, so one caller can mutate process-wide state.
- **Rust numeric model differs from Python's.** Python keeps integers exact beyond 2^53 where Rust uses 64-bit floats: in the reviewers' 89-formula battery, 32 results differed.
- **Five case-folding routines disagree.** `="É"="é"` is FALSE, but `MATCH("é","É",0)` returns 1.

## Recommendations

These are ordered by dependency, not effort. The critical path runs from step 0 to 1 to 2 to 5;
steps 3 and 4 can run in parallel with step 1.

0. **Confirm the checkpoint policy.**
   - Pause new-family runs; the loop is at Run 20 of 30.
   - Consider amending plan step 6 to allow local-only commits for each accepted run (no push). That gives you diffs and rollback and lets the 78 backup copies retire.
1. **Build the Excel oracle (plan step 4).** The pieces already exist.
   1. Write each fixture to its own sheet with `Workbook.set_value` and `set_formula`.
   2. Open, recalculate and save once in the installed Excel. AppleScript can do this, or you can do it by hand once.
   3. Read Excel's saved results back with `Workbook.get()`, which already returns the cached `<v>` value, and diff them against the fixtures.
   4. Start with the 1,286 fixtures, the repro formulas in the evidence folder and the disputed cases in section 8.
   5. Keep LibreOffice as a CI-friendly second oracle, knowing where it deviates from Excel (the harness is preserved).
2. **Build one "Excel value layer" per engine,** with expected values taken from the oracle rather than the author:
   - blank equals `""` and 0;
   - type order numbers < text < logicals, and TRUE ≠ 1;
   - 15-significant-digit number-to-text conversion and comparison;
   - 64-bit floats with #NUM! for any non-finite result;
   - truncation of integer arguments;
   - UTF-16 `LEN`;
   - one shared case-folding routine;
   - `""` is not coercible to a number;
   - errors resolve left to right.

   Then fix the engine-specific bugs in section 2.
3. **Harden,** independent of step 1:
   - a nesting-depth cap at parse time plus an 8,192-character formula cap;
   - a 32,767-character cap on every text result;
   - a work budget per evaluation;
   - a linear-time wildcard matcher;
   - a Rust cell index built once (fixes both the O(n²) reads and the nondeterminism);
   - rejection of non-finite inputs;
   - zip reading under real byte and entry caps.
4. **Gates:**
   - add the Python-vs-Rust differential fuzz and the oracle diff to `eval_command`;
   - split the fixture test into one case per fixture;
   - replace prose-substring catalog tests with structured fields such as `excel_verified: false`;
   - add schema validation;
   - fix fmt and clippy.
5. **Re-aim the loop:**
   - Replace the saturated 6/6 score with continuous metrics: oracle-verified fixture count, oracle agreement % and usage-weighted coverage.
   - Follow the plan's family order.
   - Deepen the roughly 40 most-used functions before adding new families. Next up:
     - statistics basics;
     - lookup/reference (CHOOSE, ROW/COLUMN, UNIQUE/SORT);
     - TEXT/VALUE/TODAY/NOW and SUMPRODUCT;
     - NPV/IRR/XIRR/RATE;
     - LET/LAMBDA.
   - Study the openpyxl tokenizer, CellRune and rxls, as the plan asked.
6. **Structure:**
   - a function registry that drives arity, dispatch and catalog status;
   - split each engine by function family;
   - generate the count tables and status docs from data;
   - keep a separate per-run CHANGELOG;
   - fix the packaging (`tests` as a top-level package) and catalog loading (`importlib.resources` plus deep copies).

**Lesson for agent-loop design.** The loop optimized what it could measure, which was internal
consistency, and that rubric maxed out at Run 1. It skipped the one step that needed an external
oracle, so it produced 20 runs of increasingly careful disclaimers while both engines kept the same
blind spots in the basics. An autonomous loop needs an external oracle and a metric that keeps
rising, or it drifts into ceremony.

## Evidence index

Moved on 2026-09-25 to `_archive-2026-09-25-L1/_working-files/repo-health-evidence-2026-09-24/` (git-ignored; see
that archive's `MANIFEST.tsv` and `ROLLBACK.sh`). Originally 46 files (1.6 MB), plus the re-measurement folder. Raw corpora, build
directories, crafted malicious `.xlsx` samples and install trees were not copied.

- `diff-fuzz/` — the Python-vs-Rust differential fuzzer: `gen_corpus.py`, `run_fuzz.py`, `analyze.py`, `classify.py`, the Rust runner, `root_causes.json`, `clusters.json` and verified `repros*.jsonl`. Seeds 20260924 and 7 were used.
- `lo-oracle/` — the LibreOffice harness: headless, isolated profile, run as an in-process Python macro because LibreOffice's bundled Python was killed at launch on the macOS review host. Also `results.jsonl` (per-case verdicts), `disagreements.tsv` and `family_table.tsv`.
- `rust-review/` — the isolated-process probe crate (with a capped allocator), the depth bisector, the JSON-reader checks and the Python comparison scripts.
- `py-review/` — the Python probes: the ReDoS timing, the recursion thresholds, the AST statistics, and generators for hostile `.xlsx` files (generated files not included).
- Caveat: 19 of these files hard-code temporary review paths, and the Cargo manifests point at a snapshot path. Before reuse, repoint them at `../../rust` and `../../python`.

## Activity

### 2026-09-24T21:30:00-04:00 — claude-code/Claude
- Changed: added the progress check for Runs 21–22 at the top of this file and refreshed the header. Added `repo-health-evidence-2026-09-24/remeasure-after-runs-21-22/` (25 scripts and summaries).
- Why/where: recheck Run 22 progress against a 21:00 snapshot with isolated Cargo target directories.
- Evidence: gates were re-run, the differential fuzz was replayed on the same corpus, a 3,000-case SORT/UNIQUE differential was run, and LibreOffice was run on the 76 new fixtures. I reproduced the headline checks in both engines myself.
- Next/remaining: Runs 21–22 were still unaccepted at the time of the check. Re-measure after Codex records them.

### 2026-09-24T19:22:00-04:00 — claude-code/Claude
- Changed: created this analysis record and the evidence folder next to it. No other repo files were touched.
- Why/where: assess repository health against the original research brief and build plan.
- Evidence: project gates re-run on the snapshot; four independent reviewers; headline claims reproduced (marked ✔).
- Next/remaining: resolve the checkpoint-policy decisions in step 0; Codex, as coordinating writer, may integrate findings into `CONTEXT.md` after Run 20.
