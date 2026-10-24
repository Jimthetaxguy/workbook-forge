# Source graph profile 5: AVERAGE and standalone compiler packaging

The wheel now ships the compiler CLI as `workbook-forge-compile` and
`python -m workbook_forge.source_cli`, defaulting to graph-v5. The repository
compatibility launcher `tools/source_compiler.py` keeps numeric-v1 as its default;
the library `compile_workbook` keeps graph-v2 unless `version=5` is supplied.
Existing source artifacts/templates remain supported. No UI, agent, Tauri or
server is required; generated programs require only their language standard
library, with an existing rustc needed for Rust builds. Rust OOXML export requires
the separately built `compiler_roundtrip` writer adapter via `--rust-binary`.

```sh
workbook-forge-compile compile input.xlsx bundle --language rust
workbook-forge-compile translate bundle/program.rs second.py --origin rust --destination python
workbook-forge-compile emit second.py bundle/manifest.json output.xlsx --language python --inputs "{\"'Inputs'!A1\":9}"
python tools/source_graph_benchmark.py .verification/source-v5-new --profile 5 --repeats 3
```

## Exact AVERAGE profile

Independent Rust/Python implementations accept 1–255 arguments, scalar values
and bounded rectangular references/ranges, and compute sum/count. Numeric zero
counts; referenced text (including numeric text), booleans and blanks do not.
Typed errors propagate. No counted numbers returns typed #DIV/0!. Direct
non-numeric/empty text returns #VALUE!. Computed scalar arguments are treated as
values, not reference identities. Omitted slots, arrays and reference-returning
function semantics are not added by this slice.

**Primary documentation conflicts.** The current
[worksheet AVERAGE page](https://support.microsoft.com/en-us/excel/functions/average-function)
says directly entered logical/numeric-text arguments are not counted, while
[WorksheetFunction.Average](https://learn.microsoft.com/en-us/office/vba/api/excel.worksheetfunction.average)
says they are counted. This implementation provisionally counts direct booleans
as 1/0 and decimal numeric text, following the method page. That is a selected
profile, not a resolved worksheet compatibility claim. Both sources agree on
reference filtering/zero inclusion and general error behavior. The conflict is
preserved in `catalog/average-evidence.v1.json` and the semantic catalog.

Numeric text accepts ASCII decimal/exponent syntax and ASCII space/tab/CR/LF
trimming; locale/currency/thousands and broader Unicode whitespace are unverified.
Accumulation is finite binary64 with a left fold, then division by count.
Overflow returns #NUM!, even where a different numerical algorithm could avoid
it. Empty-count error codes, mixed-error precedence, computed origins and Excel
precision remain explicitly unobserved. The native primitive value API retains
its legacy scalar filtering, independently of worksheet/source direct arguments.

## Checked writer output

Source execution is compared against the Python SDK, then the selected backend
recalculates/writes a private candidate. Before publication, the compiler reads a
bounded pinned copy and checks sheet order, cell declarations, formula ASTs and
typed values/errors against the lowered model and generated execution. A stale
writer or one that rewrites formulas cannot succeed merely by exiting zero or
returning plausible caches. The requested output is exclusively created only
after verification. No user directory cleanup or overwrite is introduced.

This applies to graph profiles and the numeric-v1 CLI. Publication checks do not
replace complete metadata equivalence, XSD validation or actual Excel opening.
Fresh generation and imported-baseline preservation remain distinct. The driver
uses Python for intake/translation/lowering/checks and output verification;
generated Rust execution and the Rust writer are independent native components.
No complete source route is labeled end-to-end Rust-only.

## Independent evidence and review

`fixtures/average-cases.v1.json` records numerical examples, reference counting,
provisional direct arguments, empty counts, text conversion and nine error codes.
`fixtures/source-graph.v5.json` combines SUM, IF, IFERROR, comparisons and AVERAGE
across multiple sheets. All four routes deliberately edit source expressions,
defaults and inputs, remove the original workbook and check typed results.
Writer regressions mutate caches/formulas and assert no output is published.

The comparison evidence audit covers every one of the existing 486 expressions:
72 follow primary operator definitions plus arithmetic; 168 infer operator order
from MATCH; 246 are local profiles without independent Excel evidence. None are
Excel-observed. See `catalog/compiler-comparison-evidence.v1.json` and
`tools/compiler_evidence.py --check`; implementation agreement does not promote
an expectation into primary evidence.

The compact cumulative [review brief](compiler-review-brief.md) identifies exact
interfaces, paths, invariants and unresolved risks for a separate reviewer. No
further function family was expanded after this stage. Full custom support remains
the target, with current source support limited to four function profiles and the
documented grammar. SDK declarations and historical inventory are separate counts.
