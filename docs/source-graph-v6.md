# Source graph profile 6: bounded XLOOKUP generation

Profile 6 extends the versioned generated-source dialect with XLOOKUP while
leaving profiles 2–5 unchanged. The packaged `workbook-forge-compile` command
defaults to `graph-v6`; the compatibility launcher `tools/source_compiler.py`
keeps its numeric-v1 default. The Python library's `compile_workbook()` API
still selects graph-v2 unless `version=6` is supplied.

```sh
workbook-forge-compile compile input.xlsx bundle --language rust --profile graph-v6
workbook-forge-compile translate bundle/program.rs second.py --origin rust --destination python
workbook-forge-compile emit second.py bundle/manifest.json output.xlsx --language python --inputs "{}"
```

The generated Rust and Python runtimes are independent stdlib programs. The
compiler driver, parser, source validator, intake, and lowering remain Python;
Rust export still requires the separately built `compiler_roundtrip` adapter.
Source profile versions are distinct from workbook schema/model versions.

## XLOOKUP argument contract

Profile 6 retains Microsoft's six positional parameters:

```text
=XLOOKUP(lookup_value, lookup_array, return_array, [if_not_found], [match_mode], [search_mode])
```

The parser represents an omitted argument explicitly. Generated source includes
all six slots, so an omitted fallback and match mode do not shift a supplied
search mode. For example, `=XLOOKUP("pear",A1:A4,B1:B4,,,-1)` still means reverse
search. An omitted `lookup_value` means search for blank cells. The fallback is
evaluated only after a miss; an omitted fallback produces `#N/A`.

The implemented subset is deliberately narrow:

- `lookup_array` and `return_array` must be direct cell references or contiguous
  one-dimensional row/column ranges with the same number of cells.
- The result is one scalar cell value; fallback values must also be scalar.
- `match_mode` may be omitted or `0` (exact match).
- `search_mode` may be omitted or `1` (forward linear search), or `-1` (reverse
  linear search).
- Blank lookups, type-aware exact comparison, duplicate selection order, typed
  cell errors, and lazy fallback evaluation are covered by generated-runtime
  tests in both languages.

Microsoft also documents approximate match modes `-1` and `1`, wildcard mode
`2`, and binary search modes `2` and `-2`. Those valid Excel features are not
implemented here; a request for them fails with `unsupported_formula` rather
than being silently treated as exact search. Array constants and computed array
expressions are outside this source dialect. Two-dimensional returns,
multi-cell results, and worksheet spill placement are unsupported.

## Evidence and limits

`fixtures/xlookup-source-cases.v1.json` supplies independently derived expected
values for default and omitted positions, a missing lookup value, forward and
reverse duplicate selection, not-found behavior, and lazy `if_not_found`.
`python/tests/test_source_graph_v6.py` executes and translates the generated
source through all four same-language/cross-language routes, checks shape and
mode refusals, and exercises XLSX export/reopen in both writer paths.

The expected values establish the local implementation profile, not an Excel
observation. Microsoft documents the syntax and broader matching/search modes in
the [XLOOKUP function reference](https://support.microsoft.com/en-us/excel/functions/xlookup-function).
No fresh Excel Desktop recalculation, no-repair opening, or broad compatibility
claim follows from this source-profile coverage.
