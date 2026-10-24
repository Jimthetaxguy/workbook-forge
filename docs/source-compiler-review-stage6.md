# Corrective review stage six

Four independent P2 reports against the stage-five checkpoint were reproduced
before correction. The unchanged review scripts were run with fresh output
directories outside the review workspace. This stage adds no function family.

## Representability before accepting styles

`source_package.py:check_fidelity` now checks actual number-format resolution,
cell-style fill assignments, application flags and inheritance. Locale-dependent
built-ins absent from the importer mapping, assigned gray125/patterned fills,
disabled application and nontrivial style inheritance are refused with
`unsupported_fidelity`. Unused default gray125 fill-table entries remain allowed.
Existing supported custom/invariant formats, solid colors, bold and alignment
continue through the route suites. This is conservative refusal, not new support
for those refused Excel features or full OOXML schema/style validation.

## Closed artifact budget

`source_contract.py:manifest_bytes` defines the exact compact encoding and checks
the existing 1 MiB budget. Graph and numeric library producers check their returned
manifests; graph lowering checks consumer input; the CLI uses the same bytes and
checks before creating a bundle directory. An oversized manifest cannot produce a
successful unusable bundle. The review's 10,001-input workbook now fits and emits;
artifacts whose exact encoding still exceeds the budget are refused. No memory
limit was raised. A caller choosing its own larger pretty-print encoding remains
responsible for that external serialization; the supported encoder is explicit.

## Signed-zero contract

The source profile retains IEEE binary64 zero sign through typed execution,
transport, SDK comparison and OOXML reimport. Numeric `-0.0` remains a float rather
than passing through `int(-0.0)`. `typed(-0.0) != typed(0.0)`, and numbers remain
distinct from booleans/blanks/errors. Writer verification keeps exact typed checks;
a deliberately corrupted zero sign is rejected before publication. This contract
is local and not a claim of observed Excel signed-zero behavior.

SUM uses an explicit ordered fold from **+0.0** in Python, generated Rust profiles
2–5 and the Rust SDK/writer. `0*-1` therefore retains negative zero, while
`SUM(0*-1)` returns positive zero. Unary negation uses actual sign inversion in both generated languages, including
zero. The Python SDK now handles stored integer zero the same way; implementing
negation as `0 - value` had masked a further `=-A1` writer mismatch. Tests include
nested negation and both languages in all four source profiles.

The SDK's implicit Rust sum identity shared the
reported generated-template bug and was corrected too. Assertions inspect typed
wire results or sign bits, not ordinary numeric equality.

The corrected Python/Rust templates change the exact accepted scaffold. Previously
saved source artifacts from these profiles must be recompiled/regenerated
from their input; full-scaffold validation refuses their old template instead of
silently running a known inconsistent SUM kernel. Prior artifacts/patches are
retained as evidence. Source dialect versions and workbook schema/model versions
are unchanged. Both languages require regeneration of older exact-scaffold artifacts after this
correction.

## Verification and residual scope

See the task-local stage-six results and patch manifest for exact commands,
counts, artifact hashes and changed paths. Regressions cover the original style,
size and zero reproductions, neighboring application/inheritance refusals, all
four edited-source routes with the original absent, positive-zero SUM identity
in each Rust profile, oversized library manifests, and strict writer sign
verification. The 68-cell four-sheet stage-five routes remain a separate gate.

AVERAGE's primary-documentation conflict, the comparison evidence ledger, absent
fresh Excel recalculation/no-repair opening, the seven previously unavailable
environment checks, full custom coverage backlog and separate compiler/engine/TS
product boundaries remain unchanged. This is a correction checkpoint for separate
reviewer retesting, not a complete compiler or Excel compatibility certificate.
