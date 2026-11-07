---
type: technical-specification
status: proposed
base: main 41f28d3
summary: Make source generation one capability of Workbook Forge rather than the price of admission. Name three source modes as distinct contracts (structured authoring, generated source with reversible edits, host-native code around Forge), decide the generated-runtime mechanism on the WA2 equivalence matrix (keep self-contained embedded runtimes, held in lockstep by the matrix), add the reverse lowering from a source Node to the evaluator's parse tree so edits return to workbook meaning without a text round trip, define editable-source levels with the provenance each needs, settle the Rust side (generated Rust is a deployment artifact; the dynamic-loading crate is the Rust route; no Rust source compiler), and state the rule that connects engine coverage to translation coverage when the profile grows.
---

# 11 — Source route

## Problem

The source compiler turns a toolkit workbook into a Python or Rust program inside a
versioned scaffold whose runtime is embedded text (`source_graph.py:399-404`,
`:407-427`; templates `source_runtime_v2..v6.{py,rs}`). Profile 6 covers five
functions (`source_graph.py:31-38`) against 123 catalogued (`catalog/formulas.json`).
An edited program is accepted only if it regenerates byte-identically (`:537-538`);
the recognised edits are expression constructors and default literals (`:523-533`).
The forward lowering starts from the evaluator's own parse tree (`expressions.py:95-114`;
`source_graph.py:307`, `:602`), but the reverse goes through text: `formula_text`
renders a `Node` to a string (`:625-641`) that `lower` stores (`:679`), `emit`
re-parses through `WorkbookModel.calculate()` (`:793`) and `verified_export` parses
again on reimport (`:768`). The generated runtimes carry their own `xlookup` and
`average_values` (`source_runtime_v6.py:257`, `:320`; `.rs:159`, `:202`), the only
copies in the tree that reimplement behaviour rather than list names (spec 08 §5).
The Rust half is generated source compiled by the system `rustc`
(`source_process.py:54`), not the crate; no Rust source compiler exists (spec 08 §4b).
WA2 measured the consequence: 79 cases, 68 agree, 5 refuse by design, 4 semantic
differences, 2 shared deviations
(`_working-files/20261106-workbook-forge-source-equivalence/results.json`). Spec 08
§7.4 and §8 defer the runtime question to that data; spec 09 step 5 names this spec
as its consumer; commentary §7 and §12 Q3/Q4 state the direction settled here.

## Outcome

- A caller knows which of three source modes they are in and what each returns to
  Excel, from a contract rather than a refusal message.
- The runtime mechanism is decided on evidence; its maintenance rule is a test.
- A source `Node` returns to the evaluator's parse tree directly; one renderer
  (`expressions._render`) serves primitives, compiler and export; `formula_text` retires.
- A generated program carries enough provenance to locate an edit in the source
  workbook without restoring formulas from the manifest.
- Adding a function to the profile is one checklist that keeps engine coverage and
  translation coverage connected.

## Scope

Modes, runtime mechanism, reverse lowering and provenance, editable-source levels,
the Rust side, the growth rule, acceptance and dependencies. Proposal only; no code,
no profile 7.

## Non-goals

- Translating arbitrary Python or Rust into a workbook (compiler-pipeline.md:75-80).
- Composition-to-cells placement (spec 12), the preservation envelope (10), the
  owner-model decision (09 G-A); any Excel observation (the two shared deviations
  wait on the 01 lane).
- Changing a shipped template `source_runtime_v2..v6`: a template edit invalidates
  every saved artifact of that profile (`docs/source-compiler-review-stage6.md:49-54`).

## Design

### 1. Three source modes, three contracts

| Mode | Today | Returns to Excel | Does not return |
| --- | --- | --- | --- |
| **A. Structured authoring** through the primitives API: `Expression.input/literal/call/binary` compile straight to the evaluator AST (`primitives.py:1-5`, `:289-326`; Rust `rust/src/primitives.rs:1-5`); nine functions (`primitives.py:27`) | Exists in both languages; no path to cells: `_render` raises on `input`/`native_value` nodes (`expressions.py:224`); README.md:97-99 calls the APIs separate | Nothing today. Target: a composition plus a binding plan is placed into cells by spec 12; all nine functions are evaluated by both engines so placement is a rendering problem, not a semantics problem | A named matrix input needs a destination range, which is the result-range contract (01 open question); until then matrix-bound compositions refuse placement |
| **B. Generated source with reversible edits**: today's scaffold | `compile`, `translate`, `emit` (`source_cli.py:211-228`); four routes consume the preceding edited artifact (`docs/compiler-review-brief.md:30-32`) | Everything `validate` accepts (`source_graph.py:168-304`): five functions, `+ - * /`, six comparisons, unary signs, cell and range references, multi-sheet from v3, typed defaults; styles return through the manifest's `model`, never through the source (`:607`, `:617-620`) | Any construct outside the dialect (`:482`); formulas are never restored from a manifest (`:619`, `:671-672`); defined names are refused at compile (`:602` parses with no name scope) |
| **C. Host-native code around Forge**: an application imports the library and loads workbooks as data (`toolkit.WorkbookModel(document=…, backend=…)` `toolkit.py:58-90`; Rust `toolkit::WorkbookModel`/`Session`; WASM `WorkbookSession` `rust/src/wasm.rs:12-15`; native bridge `native/src/lib.rs:1-6`) | Exists; it is how every agent, browser and bridge consumer works | The model, through the existing writers; the receipt names what was preserved, refused or lost (compiler-pipeline.md:203-207) | The host code itself. A registered extension with declared inputs, outputs, effects and a formula lowering is a future contract; until it exists, the nonportable portion is named in the loss report, not approximated |

Rule: a mode-B artifact that stops regenerating is not an error in the artifact; it
has become mode C. The diagnostic must say so (section 4, "what the caller is told").

### 2. The runtime mechanism, decided on the WA2 matrix

Facts the decision rests on. Both generated runtimes agree with each other on all 79
rows and with the Rust engine on every row; the Python engine is alone in all four
semantic differences (README.md:12 of the WA2 record; `results.json` `divergences`):

| Difference | Python engine | Rust engine and both runtimes | Where |
| --- | --- | --- | --- |
| XLOOKUP single-cell `lookup_array` | `#VALUE!`: "lookup_array must be a cell range" (`__init__.py:3566`) | one-cell vector, returns 10.0 (`source_runtime_v6.py:224-229`; `.rs:134-141`; documented `docs/source-graph-v6.md:36-37`) | engine refuses what the dialect documents |
| `match_mode` 0.5 | tests `match != 0` before integrality (`__init__.py:3569-3578`) → session refusal `unsupported_formula` | integer check first → `#VALUE!` (`lib.rs:6156-6171`, `:6247-6265`; `source_runtime_v6.py:245-254`) | order of two checks |
| `search_mode` 1.5 | same ordering | same | same |
| `0 * -1` | `0.0` (integer-valued operands stay exact) | `-0.0` (binary64) | invisible in Excel display |

| Option | Cost | Effect on the four differences | Artifact portability | v2–v6 artifacts |
| --- | --- | --- | --- | --- |
| (i) Generated programs import the installed `workbook_forge` engine | Breaks the stated contract "generated code has no Forge runtime dependency" (`source_cli.py:3`). Rust needs the crate as a dependency: `rustc` is invoked with no `--extern` (`source_process.py:54`), and the crate is built from the repository (`rust/Cargo.toml:1-16`), so the Rust route becomes a cargo project per artifact. A frozen artifact's meaning then changes whenever the installed engine changes | Generated Python inherits the Python engine, generated Rust the Rust engine, so the four disagreements move inside mode B: `translate` would produce programs that disagree with each other, a regression from today's 79/79 runtime agreement | Lost: an artifact needs a compatible install, and the four cross-language routes need both toolchains | Old scaffolds keep importing only while v2–v6 templates stay shipped; v7 artifacts cannot translate to or from v6 without regeneration |
| (ii) Self-contained embedded runtimes, held in lockstep by the equivalence matrix (status quo plus a maintained relationship) | Four bodies per function (two engines, two runtimes); `test_source_equivalence.py` is the only connection, so it must become the growth gate (section 6). Templates stay frozen (`docs/compiler-review-brief.md:20`); growth adds v7, never edits v6 | Unchanged and pinned: every disagreement stands exactly as recorded in `DIVERGENCES` (`test_source_equivalence.py:44-58`); whether the Python engine is corrected is a separate decision (open question 1) | Kept: stdlib Python run with `-I` (`source_process.py:71`); Rust with bare `rustc` | Kept unchanged: `import_source` sniffs the version by template prefix (`source_graph.py:500`) |
| (iii) A generated shared semantic definition from which runtimes (and engine conformance) derive | A new artifact and generator; the engines are hand-written across 123 functions, so only tests or the small runtimes can realistically be generated; risks the "third language-specific semantics project" the commentary warns of (§9) | Forces a ruling on each difference inside the definition, then one engine is out of conformance; the same work as fixing the engine, with a generator in front of it | Same as (ii) if the generated modules stay embedded | Must reproduce v2–v6 byte-identically or ship them frozen beside the generator, which is (ii) with extra machinery |

**Recommendation: (ii).** Portability is the product reason mode B exists; (i)
spends it to remove duplication the matrix already controls, and worsens the one
property mode B has proven, cross-language agreement. The lockstep contract: the
hand-derived expectations are the oracle, never an engine (AGENTS.md house rule);
every documented mode of every profile function has a row; every disagreement is
pinned with a classification; the gate runs the Rust-engine and `rustc` columns
(`tools/gate.sh` fails on a skipped test).

**(i) wins** when James decides mode-B artifacts need not be standalone (G-D), *and*
the Python engine's three XLOOKUP/sign differences are fixed first so the generated
languages still agree, *and* the profile is about to admit functions whose bodies
cannot reasonably be duplicated (dates, locale, TEXT). **(iii) wins** only as an
evolution of (ii): if both engines adopt a generated conformance suite for C2
(commentary §6), the runtimes consume the same vectors.

### 3. Reverse lowering and provenance

`to_excel(node: Node, sheet, sheets) -> _Node` is the inverse of `from_excel`
(`source_graph.py:307-355`) into the evaluator's tree (`__init__.py:88-91`):

| `Node.kind` | `_Node` | Loss on the way back |
| --- | --- | --- |
| `num` | `("number", token)` | the source lexeme: `from_excel` keeps `float()` only (`:315`) while the parser keeps the token (`expressions.py:111-114`); `to_excel` emits `repr(float)` as `formula_text` does (`:627`) |
| `text_hex`, `boolean`, `error` | `("literal", str / bool / ErrorValue)` | none |
| `reference` / `range_ref` | `("cell", (address, sheet))` / `("range", (start, end, sheet))` | `$` anchors: `key()` canonicalises away absolute markers (`:124`), so anchors are not in the program and cannot return |
| `add sub mul div`, comparisons | `("binary", operator, …)` | none |
| `neg`, `positive` | `("unary", "-"/"+")` | none |
| `if_value` | `("call", "IF", 3 children)` | a 2-argument IF was padded with `FALSE` (`:351`); it returns as `IF(a,b,FALSE)` |
| `if_error`, `sum_values`, `average_values` | `("call", …)` | none |
| `xlookup` | `("call", "XLOOKUP", 6 children with `missing`)` | padding to six is already what the parser produces (`:347`, `:312`) |
| `blank`, `missing` outside XLOOKUP | refused, as `validate` refuses them (`:251-252`, `:227-228`) | n/a |

Laws: `from_excel(to_excel(n)) == n` for every valid `Node`;
`to_excel(from_excel(t)) == t` for every tree with canonical lexemes, no anchors and
three-argument IF. Then `lower` stores `_render(to_excel(node))` and `verified_export`
compares trees; the compiler owns one renderer (`expressions.py:174-224`) and
`formula_text` plus `canonical_sheet_references` (`:644-653`) retire. Text remains the
storage form of a formula in both model shapes (spec 08 §2 "Formula"), so the text
round trip ends inside the compiler, not at the model boundary, which moves with
spec 09 step 5.

Provenance today: sheet identities in the hex header (`:412`), `'Sheet'!A1` keys
(`:414-415`), a manifest with `model` minus formulas and caches plus
`formula_addresses` (`:607`, `:617-620`). Missing: anything tying the program to the
bytes it came from. Two optional manifest fields (old v6 manifests still lower;
`lower` refuses no unknown key, `:657-672`):

- `source_fingerprint`: sha256 of the pinned `bounded_package` bytes (`:546-558`),
  never a path (compiler-pipeline.md invariant 8).
- `formula_digests[address]`: sha256 of `render(node, "python")` per formula. It
  locates an edited cell on reimport and cannot restore a formula, so "formula source
  lives in executable expressions, never in a restoration field" (`source_graph.py:3-4`)
  holds.

Provenance stays in the manifest, not in source comments: `import_source` matches exact
line shapes (`:524-525`) and source must regenerate byte-identically, so a comment
would have to derive from `Program`, which is circular.

### 4. Editable-source levels

| Level | Recognised rewrite | How provenance survives | What the caller is told when it cannot reverse |
| --- | --- | --- | --- |
| L0 (today) | expression constructors and default literals, one per line (`:523-533`) | address keys; regeneration check (`:537-538`) | `unsupported_source: noncanonical edits outside source expressions/default values` (`:538`) |
| L1 helpers | a `WFG{v} HELPERS` section of `def helper(e): return <constructor>` (Rust `fn helper(e:&mut Engine)->V`) referenced as `helper(e)` from formulas; import inlines, then the inlined tree must be in the dialect | manifest `helpers[name] = digest of body`; regeneration re-factors the same name so the section is canonical | `irreversible_abstraction` naming the helper and the first construct outside the dialect |
| L2 formatting | whitespace, line breaks and comments that leave the parsed `Program` equal | none needed; the artifact is canonicalised on write-back | `reformatted` listing normalised lines; comments in DEFAULTS/FORMULAS are dropped with `dropped_comments` because nothing can carry them back; this level is tolerance, not preservation |
| L3 renamed inputs | `INPUTS` section binding `name -> 'Sheet'!A1`; formulas use `reference(e, name)` | the binding is the owner model's `bindings.inputs` (spec 09 §4; spec 06), carried in the manifest, not invented here | `unbound_name`, `ambiguous_binding` (two names, one cell) |
| Beyond | control flow, I/O, functions outside the profile | none | `unsupported_source` naming the construct and stating the mode change: "this artifact is host-native code (mode C); it runs, and it exports only through the model API" |

Order: L1 after the reverse lowering lands (it needs tree comparison, not text);
L3 after spec 09 step 2; L2 is independent and cheap.

### 5. The Rust side

Two Rust things, kept two. Generated Rust is a standalone binary compiled by the
system `rustc` with no crate dependency (`source_process.py:54`): a deployment
artifact for a host without Forge. The `workbook_forge` crate loads workbooks as data
at run time in every Rust consumer: toolkit sessions, the agent, the WASM
`WorkbookSession` (`wasm.rs:12-15`), the Python-facing bridge (`native/src/lib.rs:1-6`).
That dynamic-loading engine is the Rust route for applications (commentary §7 table,
rows 2 and 3): a new workbook never rebuilds Forge; a new primitive does, which is
what the growth rule is for.

A Rust source compiler does not exist and is not proposed. `import_source` reads Rust
programs by token substitution into Python's `ast` (`source_graph.py:485-494`), so
mode B is Python-driven in both languages (`docs/compiler-review-brief.md:38-41`).
Revisit only for a consumer who must edit generated Rust and return it to Excel with
no Python present; the first piece is then `to_excel` in Rust over the constructor
dialect, which is the small table in section 3; a `syn`-based reader is a dependency
question under the permissive-licence rule.

### 6. The capability growth rule

Adding function F to the source profile is one pull request containing all of:

1. Precondition: F is `evaluated` or `conformance-tested` for both languages in
   `catalog/formulas.json` and in Rust `SUPPORTED_FUNCTIONS`
   (`tools/compiler_coverage.py:27-28,57`). Source coverage never leads engine coverage.
2. `FUNCTIONS_BY_VERSION[n+1]` (`source_graph.py:31-38`); lowering in `from_excel`,
   `render`, `source_node`, `to_excel`; arity in `validate`.
3. New templates `source_runtime_v{n+1}.{py,rs}` copied from v(n) plus F's bodies;
   v(n) untouched; the `.rs` listed in `pyproject.toml` package-data (`:51-52`).
4. Equivalence rows for every documented mode of F, each with a hand-derived basis;
   `test_every_profile_six_function_and_the_operators_have_cases`
   (`test_source_equivalence.py:77-81`) generalised to the highest profile; any
   disagreement pinned in `DIVERGENCES`, none hidden.
5. `docs/source-graph-v{n+1}.md`; the capability row "emit source" for F (C9) flips.

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
def test_generated_program_imports_only_stdlib(): ...           # Python: math, re, sys; Rust: use std:: only
def test_generated_rust_compiles_with_bare_rustc_no_extern(): ...

# python/tests/test_source_artifacts.py (new; fixtures/source-programs/{v2,v6}/{program.py,program.rs,manifest.json})
def test_committed_v6_artifact_still_imports_translates_and_emits(): ...
def test_committed_v2_artifact_still_imports(): ...
def test_old_manifest_without_digests_still_lowers(): ...

# python/tests/test_source_reverse_lowering.py (new)
def test_to_excel_inverts_from_excel_on_every_fixture_formula(): ...   # source-graph.v2..v5, xlookup-source-cases.v1
def test_render_of_to_excel_parses_back_to_the_same_tree(): ...
def test_two_argument_if_returns_as_three_argument_if(): ...           # documented loss, asserted
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
def test_unsupported_edit_names_construct_and_mode_change(): ...

# python/tests/test_compiler_evidence.py (extend)
def test_every_source_function_is_evaluated_by_both_engines(): ...     # growth rule step 1
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

1. **Python engine or profile?** The Python engine is alone in all four semantic
   differences and the dialect already documents single-cell arrays. Recommendation:
   fix the engine (one-cell vector; integrality before the mode check; binary64
   multiply) so the pinned divergences go to zero, rather than record a permanent
   Python-engine profile.
2. **Is standalone portability of generated artifacts a commitment (G-D)?** The
   runtime recommendation rests on it; if not, option (i) reopens.
3. **Which editable level first?** Recommendation: reverse lowering, then L1 helpers;
   L3 waits for 09 step 2.
4. **May generated program text live in the tree** as the committed v2 and v6
   fixtures the artifact tests need? It would be the first time.
