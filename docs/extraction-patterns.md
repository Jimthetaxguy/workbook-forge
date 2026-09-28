---
author: Codex
created: 2026-09-28
agent: codex/Codex
date: 2026-09-28
type: interface-contract
status: implemented
summary: Namespace-aware OOXML patterns and formula-aware extraction in independent Python and Rust implementations.
---
# Formula-aware XLSX extraction

The extraction engine reads accepted macro-free transitional XLSX packages using
each language's existing package validation. Python and Rust independently match
namespace-qualified structural paths and interpret records. Their XML tokenizers
remain standard-library XML in Python and quick-xml in Rust. No regex XML parser,
new calculation semantics, external links or macro execution is introduced.

The existing readers use the same bounded XML parsing/path primitives where
appropriate. A worksheet cell must occur directly under worksheet/sheetData/row;
cell-shaped XML under extensions is opaque content. XML parts remain at most
32 MiB, with at most 1,000,000 elements and depth 128. Existing package limits stay
in force. Formula text, literals and cached results remain distinct. Python retains the
XML encodings accepted by its existing adapter; Rust retains its UTF-8 profile.
Neither engine claims full OOXML schema validation. Duplicate direct f/v/is
payloads, nested elements under f/v, invalid stored indices, invalid name scope
and multiple owners for one table part are rejected explicitly.

## Catalog and API

catalog/extraction-patterns.json declares namespace-qualified paths for cell,
formula, defined_name, table, table_column, table_formula, validation,
merged_range, shared_string and column patterns. Formula mappings are a compact
projection of the existing function inventory and support catalog. Run
`python tools/sync_extraction_catalog.py --write` after changing those sources;
the default command checks for drift without writing. The Rust
crate embeds an identical copy; drift tests compare both copies and their source
catalogs. Mapping a function never establishes that its full formula is executable.

Python: workbook_forge.extraction.extract_xlsx(path, *, patterns=None, sheet=None,
offset=0, limit=100) -> dict; pattern_catalog() -> dict.
Rust: extraction::extract_xlsx(path, ExtractionOptions) -> Result<Json, XlsxError>;
ExtractionOptions has patterns: Option<Vec<String>>, sheet: Option<String>,
offset: u64, limit: usize (default 100); pattern_catalog() -> Json.

A selection is a nonempty unique list of catalog pattern IDs. Unknown IDs,
unknown sheets and invalid pagination fail explicitly. Limits are 1..100 records
per page, offset is u64, and booleans are not numeric controls. Each call captures
one package read; for repeated calls the caller must keep the input file stable.
Sheet filtering uses ASCII case-insensitive names and returns the canonical
worksheet name. It includes only records attached to that sheet; global records
are excluded. Tables attach to their owning sheet via tableParts relationships;
local defined names attach via valid localSheetId. Their cell locations are null.
Global names and shared strings have null sheet/cell locations. Order is lexicographic package-part order, then XML document order,
then catalog pattern order. At most 100,000 selected records may be scanned;
exceeding this refuses extraction. Serialized compact JSON is capped at 1 MiB.

The report is:

    {schema_version: 1, profile: "xlsx-extraction-v1", records: [...],
     counts: {pattern_id: count}, total_records, offset, limit,
     next_offset: integer|null, truncated: bool,
     diagnostics: [...], diagnostic_count, diagnostics_truncated}

Counts include each selected pattern with zero where absent, before pagination.
Diagnostics retain the first 100 entries and count all entries. Each diagnostic
has code, message, part, sheet and cell (nullable locations). Diagnostics apply
only to selected records, including one shared_formula entry for every unresolved
selected group member. Wording can differ;
codes and affected locations must agree. Records are:

    {pattern, part, path, sheet: string|null, cell: string|null,
     attributes: object, text: string, data: object}

Paths identify namespace-qualified nodes using local names with one-based sibling
indexes, e.g. /worksheet[1]/sheetData[1]/row[2]/c[1]/f[1]. Every matched element is
in the catalog namespace; indexes count siblings of the same expanded name.
Attributes exclude namespace declarations and use Clark names for qualified
attributes. text is direct element character data (including CDATA and child tails),
excluding descendant text. Original formula text is retained exactly after XML decoding.

## Pattern meanings

- cell: data contains value, cached_value, cell_type (default n), style_id (integer
  or null). Formula cells have value:null; their stored value is cached_value.
  Other cells have cached_value:null. Shared/inline strings resolve direct text
  and rich-text runs, excluding phonetic/extension text. Error values use
  {error: code}. No formula execution occurs.
- formula and table_formula: data contains kind (normal/shared/array/dataTable
  or table), shared_index (string|null), group_range (string|null), master_cell
  (string|null), effective_formula (string|null), analysis (below). Normal/array/
  table expressions retain their source text as effective_formula. Shared
  followers resolve from the unique master and relative cell offsets using the
  existing formula-copy implementation. The derived text is inspection-only;
  imported grouped formulas remain protected from calculation and edits.
- shared_string: data contains value, resolving the same rich text rules.
- validation: data contains formula1 and formula2 (strings|null).
- defined_name: data contains analysis of its expression, without interpreting
  it as an application binding or enabling defined-name evaluation.
- other patterns: data is {}; attributes/text and source location expose meaning.

Formula analysis is {status: parsed|unsupported|unresolved, functions: [...],
references: [...], categories: [...]}. Successful parsing uses the existing
formula AST; references preserve absolute/relative row/column flags. Function
records are {name, known: bool, category: string|null, python: status,
rust: status}, with statuses from the support catalog or catalogued/unknown.
Function records and categories are sorted uniquely. An unsupported parse keeps
raw text, empty analysis collections and a formula_syntax diagnostic. An
unresolved shared group has no effective_formula and shared_formula diagnostics.
Do not guess references or functions from text in string literals.

Shared groups require a nonnegative decimal si within u32, interpreted numerically
(01 and 1 identify the same group while raw strings are retained), exactly one master carrying
nonempty text and a valid ref range, and master/followers within that range.
Follower source text is preserved but master text governs derived meaning. No
range is expanded into implicit formula records. Missing/duplicate masters,
invalid ranges/indices and unsupported copy syntax produce explicit diagnostics.
Any invalid listed member or failed copy makes the entire group unresolved,
including the master; all original text remains available. Members are validated
before pagination so a page cannot hide conflicting group evidence.
Array/table expressions remain inspection-only regardless of parsing success.

## Evidence and verification

The contract derives shared-group interpretation from Microsoft's CellFormula
reference and structural patterns from SpreadsheetML documentation:

- [CellFormula and shared groups](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.cellformula)
- [SpreadsheetML structure](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/structure-of-a-spreadsheetml-document)
- [Shared strings](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/working-with-the-shared-string-table)

The 82 cross-language checks cover namespace-prefix variation, extension decoys,
entities/CDATA/rich strings, shared master/follower copying with mixed references,
malformed groups, unsupported syntax/functions, table/name/validation extraction,
pagination/filtering and resource limits. Existing reader, calculation, agent
and preservation regressions pass in the complete 640-test Python and 78-test Rust
suites. Minimum Rust 1.88 tests also pass. The generated scenario yields identical
Python/Rust records for 12 formulas and 3 validations. Local reports and gate
logs are in .verification/xml-engine. Installed-package receipts are under
.verification/xml-packages. Excel Desktop acceptance remains separate.
