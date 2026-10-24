# Source graph profile 4: comparisons

Profile 4 adds `=`, `<>`, `<`, `<=`, `>` and `>=` to the existing multi-sheet
source dialect. It retains three source functions (SUM, IF, IFERROR); six
operators are grammar coverage, not six more implemented Excel functions.
Numeric-v1 and graph-v2/v3 remain available, with their runtime templates frozen.
Workbook schema/model versions do not change.

```sh
python tools/source_compiler.py compile input.xlsx bundle --language rust --profile graph-v4
python tools/source_compiler.py translate bundle/program.rs second.py --origin rust --destination python
python tools/source_compiler.py emit second.py bundle/manifest.json output.xlsx --language python --inputs "{\"'Inputs'!A1\":9}"
python tools/source_graph_benchmark.py .verification/source-v4-new --profile 4 --repeats 3
```

Library callers select `source_graph.compile_workbook(..., version=4)`.
Comparison expressions are real constructors, such as
`greater_equal(reference(e, "'Inputs'!$A1"), num(5.0))`. The next translator
consumes the edited artifact and emits corresponding Excel formula text. It
never restores formulas from the manifest or original workbook.

## Explicit scalar comparison contract

| Operands | Profile 4 rule |
| --- | --- |
| Finite numbers | Binary64 numerical order, signed zeros equal; no epsilon or display-format comparison |
| Different nonblank types | Number < text < Boolean; types stay distinct (`1=TRUE` and `9="9"` are FALSE) |
| Booleans | FALSE < TRUE, with Boolean identity retained |
| Two text values | Both ASCII: ignore ASCII case; otherwise exact UTF-8 ordinal comparison |
| Blank vs number | Compare as numeric zero |
| Blank vs text | Compare as empty text |
| Blank vs Boolean | Compare as FALSE |
| Two blanks | Equal |
| Errors | Left error first, otherwise right error; finite-number checks preserve existing error precedence |

Numeric text stays text for both literal and referenced operands. Referenced
blanks adapt to the counterpart type; they are distinct from a cell containing
empty text. A formula that returns a blank reference becomes numeric zero in the
worksheet, so a later reference observes zero. Formatting does not change type.
There are no implicit wildcards or generic truthiness conversions in comparison.
IF remains lazy; comparison operands are evaluated, while unselected IF branches
are not. Error results can be handled by IFERROR; implementation failures remain
diagnostics. Arithmetic binds before comparisons, and chained comparisons follow
the existing left-associative Excel parser rather than Python chain semantics.

The worksheet SDKs now use this typed profile. The native primitive API retains
its explicitly different legacy comparison contract; callers are not silently
migrated from native Boolean-to-number coercion. Array-valued worksheet operator
results also use the worksheet rule, without adding source array/spill support.
Two historical shared profile cases were deliberately updated and annotated.

## Evidence and limits

Microsoft documents the six Boolean-result operators and precedence in
[calculation operators](https://support.microsoft.com/en-us/excel/calculation-operators-and-precedence-in-excel).
The [MATCH documentation](https://support.microsoft.com/en-us/excel/functions/match-function)
provides a compatible numeric/text/Boolean sort order. Applying that order to
operators is an explicit inference, not a claim that MATCH specifies every
operator coercion. Blank adaptation and mixed-type cases are versioned,
independently stated, **unobserved Excel-targeted expectations**. DAX and VBA
rules were not used as worksheet oracles.

ASCII case-insensitivity and UTF-8 ordinal ordering are an explicit local
collation profile. Locale punctuation/Unicode ordering and case folding remain
unverified and require a future collation contract. Binary64 edge cases do not
establish Excel's calculation precision/rounding behavior. No Excel Desktop
recalculation or no-repair opening was run; package reimport and backend agreement
are separate checks, not an Excel oracle. Full custom coverage remains the target.

`fixtures/comparison-cases.v1.json` defines 24 independently stated type pairs.
All six operators are checked across available literal/reference placements:
486 formula expressions per generated language, also checked with both SDKs.
`fixtures/source-graph.v4.json` drives all four source routes, with edited
threshold/multiplier/default/sheet-name case, changed inputs, poisoned caches,
deleted original OOXML and both-backend output reimport. It includes precedence,
chaining, typed errors, mixed-sheet references and lazy branches.

The compiler remains a Python driver: Python reads OOXML, parses/translates and
lowers source, and calculates an independent comparison during checked emission.
Generated execution is independently Rust or Python, using only that language's
standard library. The selected writer recalculates and exports through its own
backend. No generated-source workflow is labeled end-to-end Rust-only. Native
Rust intake/evaluation/export and serialized-IR transfer are separate lanes.

Output remains fresh generation under conservative fidelity guards; imported
OOXML preservation-aware patching is a different existing contract. The review
in [source compiler review](source-compiler-review-stage4.md) covers concrete
fidelity/validation fixes and remaining limits. Full XSD validation, complete
metadata modeling, names/tables/arrays/spills/providers and a general-purpose
program sandbox are not delivered by this profile.

Next bounded milestone: custom AVERAGE source compilation with per-argument
origin/coercion rules, typed errors, empty ranges and the same four-route gate.
