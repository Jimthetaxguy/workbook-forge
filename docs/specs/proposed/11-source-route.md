---
type: technical-specification
status: proposed
base: main 41f28d3
summary: Make source generation one capability of Workbook Forge rather than the price of admission. Name three source modes as distinct contracts (structured authoring, generated source with reversible edits, host-native code around Forge), decide the generated-runtime mechanism on the WA2 equivalence matrix (keep self-contained embedded runtimes, held in lockstep by the matrix, resting on the portability commitment), add the reverse lowering from a source Node to the evaluator's parse tree under a stated normalisation so edits return to workbook meaning without a text round trip, define editable-source levels with the mechanism and provenance each needs, settle the Rust side (generated Rust is a deployment artifact driven from Python; the dynamic-loading crate is the Rust route for applications; no Rust source compiler), and state the rule that connects engine coverage to translation coverage when the profile grows.
---

# 11 — Source route

## Problem

The source compiler turns a toolkit workbook into a Python or Rust program inside a
versioned scaffold whose runtime is embedded text (`source_graph.py:399-404`,
`:407-427`; templates `source_runtime_v2..v6.{py,rs}`). Profile 6 covers five
functions (`source_graph.py:31-38`) against 123 catalogued at this spec's base
commit `41f28d3` (`catalog/formulas.json`; the current count is in `CONTEXT.md`).
An edited program is accepted only if it regenerates byte-identically (`:537-538`);
the recognised edits are expression constructors and default literals (`:523-533`).
The forward lowering starts from the evaluator's own parse tree (`expressions.py:95-114`;
`source_graph.py:307`, `:602`), but the reverse goes through text: `formula_text`
renders a `Node` to a string (`:625-641`) that `lower` stores (`:678`), `emit`
re-parses through `WorkbookModel.calculate()` (`:793`) and `verified_export` parses
again on reimport (`:768`). The generated runtimes carry their own `xlookup` and
`average_values` (`source_runtime_v6.py:257`, `:320`; `.rs:159`, `:202`), the only
copies in the tree that reimplement behaviour rather than list names (spec 08 §5).
The Rust half is generated source compiled by the system `rustc`
(`source_process.py:54`), not the crate; no Rust source compiler exists (spec 08 §4b).
WA2 measured the consequence: 79 cases, 68 agree, 5 refuse by design, 4 rows where the
Python session disagrees with the Rust engine, 2 shared deviations (the matrix and its
pinned divergence table are in the tree: `tools/source_equivalence_probe.py`,
`python/tests/test_source_equivalence.py`, merged as PR #102). Spec 08
§7.4 and §8 defer the runtime question to that data; spec 09 step 5 names this spec
as its consumer.

Direction this spec takes as its premise: source generation is a capability a caller
may choose, not the price of using a workbook outside Excel; an already compiled
engine loads workbooks as data; a generated Rust program is a separate deployment
artifact that is compiled, while Forge itself is not rebuilt per workbook.

## Outcome

- A caller knows which of three source modes they are in and what each returns to
  Excel, from a contract rather than a refusal message.
- The runtime mechanism is decided on evidence; its maintenance rule is a test.
- A source `Node` returns to the evaluator's parse tree directly under a stated
  normalisation; one renderer (`expressions._render`) serves primitives, compiler and
  export; `formula_text` retires.
- A generated program carries enough provenance to locate an edit in the source
  workbook without restoring formulas from the manifest.
- Adding a function to the profile is one checklist that keeps engine coverage and
  translation coverage connected.

## Scope

Modes, runtime mechanism, reverse lowering and provenance, editable-source levels,
the Rust side, the growth rule, acceptance and dependencies. Proposal only; no code,
no profile 7.

## Non-goals

- Translating arbitrary Python or Rust into a workbook (`docs/specs/compiler-pipeline.md:75-80`).
- Composition-to-cells placement (spec 12), the preservation envelope (10), the
  owner-model decision (09 G-A, decided 2026-11-09); any Excel observation (the two shared deviations
  wait on the 01 lane).
- Changing a shipped template `source_runtime_v2..v6`: a template edit invalidates
  every saved artifact of that profile (`docs/source-compiler-review-stage6.md:49-54`).

## Design

### 1. Three source modes, three contracts

| Mode | Today | Returns to Excel | Does not return |
| --- | --- | --- | --- |
| **A. Structured authoring** through the primitives API: `Expression.input/literal/call/binary` compile straight to the evaluator AST (`primitives.py:1-5`, `:289-326`; Rust `rust/src/primitives.rs:1-5`); nine functions (`primitives.py:27`) | Exists in both languages; no path to cells: `_render` raises on `input`/`native_value` nodes (`expressions.py:225`); README.md:97-99 calls the APIs separate | Nothing today. Target: a composition plus a binding plan is placed into cells by spec 12; all nine functions are evaluated by both engines so placement is a rendering problem, not a semantics problem | A named matrix input needs a destination range, which is the result-range contract (01 open question); until then matrix-bound compositions refuse placement |
| **B. Generated source with reversible edits**: today's scaffold | `compile`, `translate`, `emit` (`source_cli.py:211-228`); four routes consume the preceding edited artifact (`docs/compiler-review-brief.md:30-32`) | Everything `validate` accepts (`source_graph.py:168-304`): five functions, `+ - * /`, six comparisons, unary signs, cell and range references with their `$` anchors, multi-sheet from v3, typed defaults; styles return through the manifest's `model`, never through the source (`:607`, `:617-620`) | Any construct outside the dialect (`:482`); formulas are never restored from a manifest (`:619`, `:670-671`); defined names are refused at compile (`:602` parses with no name scope) |
| **C. Host-native code around Forge**: an application imports the library and loads workbooks as data (`toolkit.WorkbookModel(document=…, backend=…)` `toolkit.py:58-90`; Rust `toolkit::WorkbookModel`/`Session`; WASM `WorkbookSession` `rust/src/wasm.rs:12-15`; native bridge `native/src/lib.rs:1-6`) | Exists; it is how every agent, browser and bridge consumer works | The model, through the existing writers; the receipt names what was preserved, refused or lost (`docs/specs/compiler-pipeline.md:203-207`) | The host code itself. A registered extension with declared inputs, outputs, effects and a formula lowering is a future contract; until it exists, the nonportable portion is named in the loss report, not approximated |

Rule: a mode-B artifact that stops regenerating is not an error in the artifact, and
it has not become mode C: a drifted generated program imports only the standard
library (`source_runtime_v6.py:2-4`; `.rs:2`), holds no workbook and has no model API.
It is a fourth thing, standalone code outside Forge, and the diagnostic must say so:
"this program no longer regenerates from its scaffold; it is standalone code; nothing
in it returns to Excel" (section 4, "what the caller is told").

### 2. The runtime mechanism, decided on the WA2 matrix

Facts the decision rests on (`results.json`; README of the WA2 record). Both generated
runtimes agree with each other on all 79 rows and with the Rust engine on every row.
The Python side disagrees with the Rust engine on four rows at the session layer; two
are value differences and two are refusal-classification differences, since at the
formula API the Python and Rust engines both return `#VALUE!` on the fractional-mode
rows and only the Python session refuses them:

| Row | Python engine and session | Rust engine and both runtimes | Kind |
| --- | --- | --- | --- |
| XLOOKUP single-cell `lookup_array` | `#VALUE!`: "lookup_array must be a cell range" (`__init__.py:3566`) | one-cell vector, returns 10.0 (`source_runtime_v6.py:224-229`; `.rs:134-141`; the dialect documents it, `docs/source-graph-v6.md:36-37`) | value: the engine refuses what the dialect accepts; a Workbook Forge profile choice, not a Microsoft XLOOKUP statement (`catalog/formulas.json` has no XLOOKUP semantic spec; its only XLOOKUP source is the lookup-functions index) |
| `match_mode` 0.5 | engine `#VALUE!` (`match != 0`, no integrality check, `__init__.py:3569-3578`); session refuses `unsupported_formula` | `#VALUE!` after an integrality check (`lib.rs:6156-6171`, `:6247-6265`; `source_runtime_v6.py:245-254`) | classification: refusal versus an error value; undecidable without Excel, whose documentation lists valid values only |
| `search_mode` 1.5 | same | same | same |
| `0 * -1` | `0.0` (integer-valued operands stay exact) | `-0.0` (binary64) | value; unobservable in Excel's display |

| Option | Cost | Effect on the four rows | Artifact portability | v2–v6 artifacts |
| --- | --- | --- | --- | --- |
| (i) Generated programs import the installed `workbook_forge` engine | Breaks the stated contract "generated code has no Forge runtime dependency" (`source_cli.py:3`). Rust needs the crate linked: `rustc` is invoked with no `--extern` or `-L` (`source_process.py:54`); the crate builds an `rlib` (`rust/Cargo.toml:16`), so linking without cargo is possible but unimplemented. A frozen artifact's meaning changes whenever the installed engine changes | **Not measured.** No column of the matrix runs a generated program that imports an engine. If generated Python bound the Python engine, the four rows would move inside mode B; if it bound the Rust engine through the installed bridge (`WorkbookModel(backend="rust")`, `toolkit.py:65`), all 79 would agree. Either way the rows are the already tracked engine-parity gap, not a new one | Lost: an artifact needs a compatible install, and the four cross-language routes need both toolchains | Old scaffolds keep importing only while v2–v6 templates stay shipped. Cross-version translation does not exist under any option (`translate` preserves the program's version, `:542-543`) |
| (ii) Self-contained embedded runtimes, held in lockstep by the equivalence matrix (status quo plus a maintained relationship) | Four bodies per function (two engines, two runtimes); `test_source_equivalence.py` is the only connection, so it must become the growth gate (section 6). Templates stay frozen (`docs/compiler-review-brief.md:20`, which lists v2 to v5 and is itself stale by one profile); growth adds v7, never edits v6 | Unchanged and pinned: every disagreement stands exactly as recorded in `DIVERGENCES` (`tools/source_equivalence_probe.py:360-414`, used by `test_source_equivalence.py:44-58`); whether the Python side is corrected is a separate decision (open question 1) | Kept: generated Python imports only the standard library today (`source_runtime_v6.py:2-4`) and is run with `-I` (`source_process.py:71`), which isolates the environment and user site but not site-packages; the property that exists is "template-frozen" (`test_source_graph.py:114` rejects `import os`), and the proposed stdlib-only test would be the first explicit check. Rust compiles with bare `rustc` | Kept unchanged: `import_source` sniffs the version by template prefix (`source_graph.py:500`) |
| (iii) A generated shared semantic definition from which runtimes (and engine conformance) derive | A new artifact and generator; the engines are hand-written across 123 functions at this spec's base commit `41f28d3` (the current count is in `CONTEXT.md`), so only tests or the small runtimes can realistically be generated; a third place where spreadsheet semantics are written | Forces a ruling on each row inside the definition, then one engine is out of conformance; the same work as fixing the engine, with a generator in front of it | Same as (ii) if the generated modules stay embedded | Must reproduce v2–v6 byte-identically or ship them frozen beside the generator, which is (ii) with extra machinery |

**Recommendation: (ii), resting on portability.** The measured facts are the 79/79
agreement between the generated runtimes and with the Rust engine, the stated
no-dependency contract, and bare `rustc`. The case against (i) is not that it would
regress agreement, which nobody has measured, but that it spends the one property mode
B exists for: a standalone artifact. The lockstep contract: the hand-derived
expectations are the oracle, never an engine (AGENTS.md house rule); every documented
mode of every profile function has a row; every disagreement is pinned with a
classification; the gate runs the Rust-engine and `rustc` columns (`tools/gate.sh`
fails on a skipped test).

**(i) wins** when James decides mode-B artifacts need not be standalone (G-D). In that
case the first step is one new probe column, generated Python calling
`WorkbookModel(backend=…)`, so (i) is measured rather than inferred; the Python-side
rows (open question 1) then decide which engine the generated program binds. **(iii)
wins** only as an evolution of (ii): if both engines adopt a generated conformance suite
for operation semantics, the runtimes consume the same vectors.

**What would reverse (ii):** G-D answered "no"; a profile-7 function whose body cannot
reasonably be duplicated in two runtimes (dates, locale, TEXT); or an Excel observation
contradicting the Rust engine and both runtimes on single-cell XLOOKUP, after which
lockstep-to-runtimes would entrench a wrong profile and correcting it would require a
template change, which the stage-6 precedent shows invalidates every artifact of that
profile.

### 3. Reverse lowering and provenance

`to_excel(node: Node, sheet, sheets) -> _Node` is the inverse of `from_excel`
(`source_graph.py:307-355`) into the evaluator's tree (`__init__.py:88-91`), **under a
normalisation N**. `from_excel` is not injective, so a strict inverse does not exist;
the law is stated modulo N and the test compares under it.

| `Node.kind` | `_Node` | Normalisation on the way back |
| --- | --- | --- |
| `num` | `("number", token)` | the source lexeme: `from_excel` keeps `float()` only (`:315`) while the parser keeps the token (`expressions.py:111-114`); `to_excel` emits `repr(float)` as `formula_text` does (`:627`) |
| `text_hex`, `boolean`, `error` | `("literal", str / bool / ErrorValue)` | none |
| `reference` / `range_ref` | `("cell", (address, sheet))` / `("range", (start, end, sheet))` | **anchors: none.** `$` is stored verbatim by `from_excel` (`:325-326`), emitted by `render` (`:375`) and returned by `formula_text` (`:635`); `key()` (`:124`) strips it only for dependency bookkeeping (`:200`, `:261`, `:269`). `test_source_graph.py:33, 74-76` already prove `$A1`, `$A$1:A6` and `B$1:B$2` survive the edit → translate → emit route; `to_excel` must keep that. **Sheet names: canonical case and quoting.** `split_reference` resolves the name case-insensitively (`:108-110`) and `canonical_sheet_references` rewrites to the canonical spelling (`:644-653`; `test_source_graph_v4.py:61, 91`); that rewrite moves into `to_excel` |
| `add sub mul div`, comparisons | `("binary", operator, …)` | none |
| `neg`, `positive` | `("unary", "-"/"+")` | none |
| `if_value` | `("call", "IF", 3 children)` | a 2-argument IF was padded with `FALSE` by `from_excel` (`:351`); it returns as `IF(a,b,FALSE)` |
| `if_error`, `sum_values`, `average_values` | `("call", …)` | none |
| `xlookup` | `("call", "XLOOKUP", 6 children with `missing`)` | **trailing `missing` padding is `from_excel`'s** (`:347`), not the parser's: the parser emits `missing` only for explicitly empty positions (`__init__.py:786-799`), so `XLOOKUP(a,b,c)`, `(a,b,c,)`, `(a,b,c,,)` and `(a,b,c,,,)` lower to one `Node`. Today's text route already emits the trailing commas (`:637`). `to_excel` keeps six children, as the dialect documents (`docs/source-graph-v6.md:28-29`); N treats trailing `missing` as equal to absence. Six of the ten fixtures in `fixtures/xlookup-source-cases.v1.json:19-25` differ only by this |
| `blank`, `missing` outside XLOOKUP | refused, as `validate` refuses them (`:251-252`, `:227-228`) | n/a |

Laws: `from_excel(to_excel(n)) == n` for every valid `Node`;
`N(to_excel(from_excel(t))) == N(t)` for every valid tree, where N canonicalises the
numeric lexeme, pads IF to three arguments, pads XLOOKUP trailing `missing` to six and
canonicalises sheet-name case and quoting. Then `lower` stores `_render(to_excel(node))`
and `verified_export` compares trees under N; the compiler owns one renderer
(`expressions.py:174-225`) and `formula_text` plus `canonical_sheet_references` retire.
Text remains the storage form of a formula in both model shapes (spec 08 §2 "Formula"),
so the text round trip ends inside the compiler, not at the model boundary, which moves
with spec 09 step 5.

Provenance today: sheet identities in the hex header (`:412`), `'Sheet'!A1` keys
(`:413-414`), a manifest with `model` minus formulas and caches plus
`formula_addresses` (`:607`, `:617-620`). Missing: anything tying the program to the
bytes it came from. Two optional manifest fields (old v6 manifests still lower;
`lower` checks named keys only, `:657-672`):

- `source_fingerprint`: sha256 of the pinned `bounded_package` bytes (`:546-558`),
  never a path (`docs/specs/compiler-pipeline.md:215`).
- `formula_digests[address]`: sha256 of `render(node, "python")` per formula. It
  locates an edited cell on reimport and cannot restore a formula, so "formula source
  lives in executable expressions, never in a restoration field" (`source_graph.py:3-4`)
  holds.

Provenance stays in the manifest, not in source comments: `import_source` matches exact
line shapes (`:524-525`) and source must regenerate byte-identically, so a comment
would have to derive from `Program`, which is circular.

### 4. Editable-source levels

Every level above L0 replaces or extends the acceptance predicate that L0 rests on:
template prefix identity (`:500-502`; `source_cli.py:103-105` for numeric-v1) and
byte-identical regeneration (`:537-538`). None is free, and there is no write-back of a
source artifact in any route today: `translate` writes a new program (`:542-543`) and
`emit` writes `.xlsx` (`:786-812`).

| Level | Recognised rewrite | Mechanism it needs | How provenance survives | What the caller is told when it cannot reverse |
| --- | --- | --- | --- | --- |
| L0 (today) | expression constructors and default literals, one per line (`:523-533`) | none | address keys; regeneration check (`:537-538`) | `unsupported_source: noncanonical edits outside source expressions/default values` (`:538`) |
| L1 helpers | a `WFG{v} HELPERS` block of `def helper(e): return <constructor>` (Rust: a helper line shape, since `fn helper(e: &mut Engine) -> V` is not an expression and `parse_constructor`'s token substitution cannot read it, `:485-493`) referenced as `helper(e)` from formulas; import inlines, then the inlined tree must be in the dialect | a new block after FORMULAS with its own line shapes (it cannot sit between the template and the DEFAULTS marker, which `import_source` reads as the hex SHEET header, `:505-506`); a `helpers` field on `Program` (`:55-60` has none) so regeneration re-emits the same names canonically; a manifest digest cannot regenerate a body, so helpers live in the program | manifest `helpers[name] = digest of body` for change detection only | `irreversible_abstraction` naming the helper and the first construct outside the dialect |
| L2 formatting | whitespace, line breaks and comments that leave the parsed `Program` equal | replace byte-identity with parse-then-regenerate equality: a line that does not match a shape is today silently skipped (`:529-533`) and the artifact then refused by `:537-538`; L2 changes that predicate for the whole import contract, so it is a contract change, not tolerance | none needed; the caller receives the canonical regeneration as the artifact's canonical text | `reformatted` listing normalised lines; comments in DEFAULTS/FORMULAS are dropped with `dropped_comments` because nothing can carry them back |
| L3 renamed inputs | `INPUTS` section binding `name -> 'Sheet'!A1`; formulas use `reference(e, name)` | the binding is the owner model's `bindings.inputs` (spec 09 §4; spec 06), carried in the manifest, not invented here | the manifest binding | `unbound_name`, `ambiguous_binding` (two names, one cell) |
| Beyond | control flow, I/O, functions outside the profile | none | none | `unsupported_source` naming the construct and stating that the program is now standalone code outside Forge (section 1, rule) |

Order: L1 after the reverse lowering lands (it needs tree comparison, not text) and
after the `Program` change; L3 after spec 09 step 2; L2 last, because it changes the
acceptance predicate every other level relies on.

### 5. The Rust side

Two Rust things, kept two, with the asymmetry stated. Generated Rust is a standalone
binary compiled by the system `rustc` with no crate dependency (`source_process.py:54`):
a deployment artifact for a host without Forge. But mode B is Python-driven in both
languages: compile, translate and import run in Python (`source_graph.py:485-493`,
`:542`), and Rust export needs the separately built `compiler_roundtrip` adapter
(`:801-806`; `docs/source-graph-v6.md:17`). A Rust-only host therefore loses mode B
entirely, not only generated Rust; a crate consumer without generated Rust loses only a
Forge-free deployment binary and `rustc`'s checking of a frozen program.

The `workbook_forge` crate loads workbooks as data at run time in every Rust consumer:
toolkit sessions, the agent, the WASM `WorkbookSession` (`wasm.rs:12-15`), the
Python-facing bridge (`native/src/lib.rs:1-6`). That dynamic-loading engine is the Rust
route for applications (mode C): a new workbook never rebuilds Forge; a new primitive
does, which is what the growth rule is for.

A Rust source compiler does not exist (`rust/src/lib.rs:7-21` lists no source module;
`rust/examples/compiler_roundtrip.rs` is a toolkit-IR adapter) and is not proposed.
Revisit only for a consumer who must edit generated Rust and return it to Excel with
no Python present; the first piece is then `to_excel` in Rust over the constructor
dialect, which is the table in section 3; a `syn`-based reader is a dependency
question under the permissive-licence rule.

### 6. The capability growth rule

Adding function F to the source profile is one pull request containing all of:

1. Precondition: F is `conformance-tested` for both languages in
   `catalog/formulas.json` (the only status value the catalog uses for its
   functions; 123 at this spec's base commit `41f28d3`, and the current count
   is in `CONTEXT.md`) and present in Rust `SUPPORTED_FUNCTIONS` (`toolkit.rs:26-150`).
   `tools/compiler_coverage.py:56` accepts a looser set (`implemented`, `evaluated`,
   `conformance-tested`); the rule uses the catalog's actual status. Source coverage
   never leads engine coverage.
2. `FUNCTIONS_BY_VERSION[n+1]` (`source_graph.py:31-38`); lowering in `from_excel`,
   `render`, `source_node`, `to_excel`; arity in `validate`.
3. New templates `source_runtime_v{n+1}.{py,rs}` copied from v(n) plus F's bodies;
   v(n) untouched; the `.rs` listed in `pyproject.toml` package-data (`:51-52`).
4. Equivalence rows for every documented mode of F, each with a hand-derived basis;
   `test_every_profile_six_function_and_the_operators_have_cases`
   (`test_source_equivalence.py:77-81`) generalised from the hard-coded `[6]` to the
   highest profile; any disagreement pinned in `DIVERGENCES`, none hidden.
5. `docs/source-graph-v{n+1}.md`; the capability row "emit source" for F flips in the
   capability matrix once that lane merges.

Steps 1 and 4 connect the coverages: engines and runtimes run the same rows against
the same expectations.

## Schema / model-version impact

None. `schema_version` and `model_version` stay 1; source profile versions are separate
(`docs/source-graph-v6.md:17-18`). Sections 1 to 5 fit profile 6; only a new function
creates profile 7. The manifest is not schema-governed (`source_contract.py:14-19`);
the two new fields are optional and additive.

## Acceptance tests

```python
# python/tests/test_source_equivalence.py (existing; the lockstep gate)
def test_engine_and_generated_runtime_agree(case): ...          # 79 rows green; divergences pinned
def test_generated_program_imports_only_stdlib(): ...           # Python: math, re, sys; Rust: use std:: only (first explicit check)
def test_generated_rust_compiles_with_bare_rustc_no_extern(): ...

# python/tests/test_source_artifacts.py (new; fixtures/source-programs/{v2,v6}/{program.py,program.rs,manifest.json})
def test_committed_v6_artifact_still_imports_translates_and_emits(): ...
def test_committed_v2_artifact_still_imports(): ...
def test_old_manifest_without_digests_still_lowers(): ...

# python/tests/test_source_reverse_lowering.py (new)
def test_to_excel_inverts_from_excel_under_normalisation_on_every_fixture_formula(): ...   # source-graph.v2..v5, xlookup-source-cases.v1; six XLOOKUP fixtures differ only by trailing missing
def test_anchors_survive_to_excel(): ...                                # $A1, $A$1:A6, B$1:B$2, as test_source_graph.py:74-76 proves today
def test_sheet_names_return_canonical_case_and_quoting(): ...
def test_render_of_to_excel_parses_back_to_the_same_tree(): ...
def test_two_argument_if_returns_as_three_argument_if(): ...           # documented normalisation, asserted
def test_verified_export_accepts_tree_equal_text_different(): ...      # "(A1+1)" vs "A1+1"
def test_verified_export_refuses_tree_difference(): ...                # backend_mismatch

# python/tests/test_source_provenance.py (new)
def test_manifest_carries_fingerprint_and_digests_but_no_formula(): ...
def test_fingerprint_is_never_a_path(tmp_path): ...                    # private-dir name absent from manifest bytes
def test_edit_report_locates_edited_cell_by_digest(): ...

# python/tests/test_source_levels.py (new, one per level as it lands)
def test_helper_level_inlines_and_regenerates_canonically(): ...
def test_helper_outside_dialect_reports_irreversible_abstraction(): ...
def test_formatting_level_canonicalises_and_reports_reformatted(): ...
def test_renamed_inputs_resolve_through_owner_bindings(): ...
def test_unsupported_edit_names_construct_and_standalone_status(): ...

# python/tests/test_compiler_evidence.py (extend)
def test_every_source_function_is_conformance_tested_by_both_engines(): ...   # growth rule step 1
```

Docs-only change: `python -m ruff check python tools examples setup.py` and
`python -m pytest -q python/tests/test_project_policy.py` pass.

## Dependencies

- Spec 08 (§7.4 and §8 route the runtime question here); WA2 results and
  `tools/source_equivalence_probe.py` as merged in PR #102.
- Spec 09 step 5 consumes section 3, which itself needs nothing from 09; L3 needs 09
  step 2 and spec 06; the fingerprint's name should match `metadata.source` in 09 §4
  (spec 10). Spec 12 for mode A's return path; spec 01 for the two shared deviations.

## Open questions for James

1. **The four Python-side rows are three different kinds of question.** Single-cell
   `lookup_array`: a Workbook Forge profile choice (the dialect accepts a reference as a
   one-element vector, `source_graph.py:148-151`, `:231`; the catalog has no XLOOKUP
   semantic spec and no Microsoft sentence settles it); the engine and the dialect
   should agree, and the recommendation is to align the Python engine with the dialect
   and the Rust engine, labelled as a profile. Fractional `match_mode` and
   `search_mode`: undecidable without Excel, a choice between a refusal and an
   unverified `#VALUE!`; recommendation: refuse in both, as the Python session does,
   until observed. Signed zero: unobservable in Excel; recommendation: record it as a
   profile and leave both engines. Three decisions, not one "fix".
2. **Is standalone portability of generated artifacts a commitment (G-D)?** The
   runtime recommendation rests on it; if not, option (i) reopens and is measured with
   one new probe column before it is chosen.
3. **Which editable level first?** Recommendation: reverse lowering, then L1 helpers
   with the `Program` change; L3 waits for 09 step 2; L2 last.
4. **May generated program text live in the tree** as the committed v2 and v6
   fixtures the artifact tests need? It would be the first time.

## Refutation record

An independent refuter read the first draft under the `parity` and `simplification`
lenses: 4 refuted, 11 weakened, 12 confirmed, 8 citation errors. All applied above.

| Finding | Effect |
| --- | --- |
| Refuted: anchors are lost on the way back (they are stored verbatim, emitted and returned today; `test_source_graph.py:33, 74-76`) | Section 3 row corrected; the law's "no anchors" carve-out removed; a `to_excel` that dropped anchors would regress the current route |
| Refuted: XLOOKUP padding is the parser's (it is `from_excel`'s; `from_excel` is not injective; six of ten fixtures differ only by trailing `missing`) | Law restated under normalisation N; the inversion test compares under N |
| Refuted: L2 formatting is independent and cheap, canonicalised on write-back (it replaces the acceptance predicate; there is no write-back) | Section 4 row rewritten; L2 ordered last |
| Refuted: a drifted mode-B artifact becomes mode C (it is standalone code with no Forge) | Section 1 rule and section 4 diagnostic corrected |
| Weakened: "Python engine alone in all four" (two are session refusal classifications); option (i)'s regression inferred from an unrun column and ignoring the Rust-bridge binding; "cargo project per artifact" (an `rlib` links with `--extern`); "v7 cannot translate to v6" (true of every option); "stdlib with `-I`" (`-I` does not isolate site-packages); OQ1 as one fix (it is a profile choice, an undecidable and an unobservable); the Rust summary hiding that a Rust-only host loses mode B; growth-rule gate looser than the catalog's single status; L1 helpers mechanics; sheet-name canonicalisation missing from the loss table; three versus four differences | Sections 2, 3, 4, 5, 6 and open question 1 corrected |
| Citation errors: an architecture document outside the repository cited eight times (removed; the direction is stated as this spec's premise); `DIVERGENCES` lives in `tools/source_equivalence_probe.py:360-414`; `tools/compiler_coverage.py:56`; `docs/specs/compiler-pipeline.md` path; five off-by-one lines | Corrected |
| Confirmed: the 79/79/79 agreement and the four session rows; the no-dependency contract and bare `rustc`; losses 1 and 3; no Rust source compiler; the regeneration guard; frozen templates and the stage-6 precedent; mode A's nine functions | Unchanged |

The refuter's verdict: option (ii) survives on the measured facts and the portability
commitment, not on the regression argument; section 3 had to be rewritten before its
acceptance tests could be written. Both are done above.
