# Compiler review checkpoint: stage 4

Review scope: the accumulated local compiler changes through source profile 4,
including source execution, workbook/source-map consistency, resource accounting,
semantic drift and generation/preservation claims. This is a focused code and
adversarial-fixture review, not an external security audit or Excel observation.
No additional functions were expanded after the comparison slice.

## Reproduced findings and fixes

1. The fidelity gate accepted unknown attributes on worksheet `sheetData`/`cols`
   and workbook `sheets` containers. Fresh generation dropped them. It now checks
   known worksheet-node attributes on every sheet and refuses those workbook
   container attributes. Three synthetic mutations reproduce the previous gap.
2. An unused external workbook relationship was ignored and dropped. Source
   bundles now refuse unmodeled/external relationship types and attributes,
   validate relationship identities/counts against worksheet bindings, and retain
   the conservative internal worksheet/styles relationship contract. The test
   uses an inert `example.invalid` URL; no service is contacted.
3. A floating-point profile value `2.0` passed membership checks and reached an
   unintended template filename. Integer contracts are now enforced explicitly
   for programs, runtime templates and compilation profiles. Unknown profile
   values fail with `unsupported_version`.
4. Unicode line separators in sheet names could produce source that the line-based
   declaration importer could not read. These names now fail explicitly with
   `unsupported_reference`, rather than producing an unusable artifact. U+0085
   was already refused elsewhere; the new guard gives a consistent source
   diagnostic. U+2028/U+2029 are pending a richer name-encoding dialect.

`python/tests/test_source_compiler_review.py` first reproduced eight failures
(including diagnostic consistency) and four already-protected cases. All twelve
review tests pass after the fixes. The before/after logs are preserved in the
local task. These refusals are implementation backlog, not permanent product
exclusions or changes to the global JSON duplicate-key policy.

## Boundaries checked

- Full known runtime scaffold plus canonical constructor regeneration is required
  before execution. Unknown calls in comparison source never reach subprocess
  execution in either language. Lazy branches remain subject to static validation.
- Source bytes and Rust executable are private pinned snapshots. Retained binary
  files are exclusively created with descriptor-based mode changes. Existing
  path replacement and symlink regressions remain in the aggregate suite.
- Manifest sheet order/identity, exact cell declarations and formula/input roles
  must match the source. Formula/cache restoration fields are refused; edited
  defaults and expressions remain authoritative. Metadata remains manifest-owned
  within the existing model and fidelity guards, not executable manifest code.
- ZIP expansion, source/payload limits, AST depth/node count, dependencies,
  repeated range expansion, cached/materialized reference values and execution
  timeouts remain bounded. This is not a hard whole-process memory sandbox;
  compiler/toolchain memory is separately measured, and deployment trust/path
  policies are outside this local source tool.
- Comparison semantics now dispatch on explicit types. Worksheet numbers/text/
  Booleans and counterpart-typed blanks are separated from the legacy native
  primitive profile. Both independent generated implementations and both SDKs
  consume the shared matrix; no host Boolean/number equality is the specification.
  Function-specific MATCH/SORT/criteria coercion profiles were not rewritten to
  borrow the new operator rule.
- Source output is fresh generation, with refusal of known unsupported fidelity
  content. Snapshot export of an imported immutable package is a separate lane.
  The gate is not complete OOXML XSD validation, nor evidence of Excel opening
  without repairs. No source manifest restores opaque original XML.
- Receipts identify Python intake/parser/translator/lowering/check calculation,
  independent generated evaluators and target writer recalculation. Standalone
  Rust programs require no Python; the compiler workflow still uses Python.
  Canonical export remains Python calculation/writing with optional Rust comparison.

## Remaining semantic evidence gaps

Operator syntax and precedence have Microsoft documentation. Type-order inference,
blank adaptation, locale collation and precision/error boundaries still need fresh
Excel observations; shared expectations and parity are not that oracle. Exact
UTF-8 ordinal ordering for non-ASCII text is a local profile. Dynamic arrays,
spill placement, providers, UDF bindings, iterative cycles and complete fidelity
remain separate explicit contracts. The 521-row function inventory is historical
and needs target-build reconciliation; source function coverage is still three,
with six additional grammar operators. The TS layer remains a transport-neutral
client target, not a third evaluator introduced by this work.
