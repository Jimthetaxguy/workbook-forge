# Third product foundation: TypeScript client

The optional client in [typescript/README.md](../typescript/README.md) reuses the
existing [agent-workbook-v1](agent-protocol.md) operations and independently running
Python/Rust backends. No compiler or formula-engine code changes are required.
The compiler remains independently installable and executable without TS/UI/server.

| Boundary | Exact implementation | Evidence |
| --- | --- | --- |
| Protocol identity and limits | `catalog/agent-operations.json` → `tools/generate_ts_contract.py` → `typescript/src/contract.ts` | Deterministic regeneration check |
| Transport-neutral typed client | `typescript/src/client.ts` | Strict tsc, declaration tests, response/failure/budget unit tests |
| Actual local transport | `typescript/node-transport.mjs`, `.d.mts` | Real Python/Rust child processes; request identities and bounded JSON lines |
| Existing Python adapter | `python/workbook_forge/agent.py`, `cli.py` | Real source/model intake, calculation and export |
| Existing Rust adapter | `rust/src/agent.rs`, `rust/examples/agent_workbook.rs` | Independent native process intake, calculation and export |
| Synthetic flow | `examples/ts_client_fixture.py`, `typescript/examples/synthetic.mjs`, `tools/typescript_sdk_receipt.py` | Same edits/bindings/expected values, both backends, authored and imported XLSX |
| Unsupported/cache distinction | `typescript/tests/unsupported.mjs` | Real diagnostics/unavailable results; no cache substitution/export |

The SDK is a scalar view of existing operation results. `read` is sparse authored
content; `recalculate` obtains current values from named output closures and
returns bounded observed differences. The compiler, embeddable engine and TS
integration are independently adoptable layers. This stage implements a local
process adapter rather than Tauri, FastAPI or a large spreadsheet UI. Formula
coverage, workbook fidelity and UI fidelity remain separate dimensions.

Review priorities: exact request/response identity, revision consistency across
paged observations, unknown/unsupported typed results, post-mutation uncertain
outcomes, baseline invalidation, safe integer controls, negative-zero wire values,
resource bounds and distinction between selected closures versus global changes.

## Integration-found preservation correction

The export/reimport acceptance exposed a pre-existing imported-package patch bug:
Python dictionary equality conflated Boolean/number changes, and both writers'
numeric equality conflated positive/negative zero. `python/workbook_forge/xlsx.py`
now uses existing typed serialized digests to detect imported scalar changes;
`rust/src/xlsx.rs` compares numeric bits while retaining enum type distinctions.
No tolerance was added. Six backend-independent Python regressions cover both
zero-sign directions and Boolean/number changes; the actual TS backend lanes
also exercise false→0 and +0→-0 through imported export and reimport. This touches
only package-change detection, not new functions or a third semantic engine.
The reviewed stage-six patches/receipts and reconstructed 71-file tree remain
preserved; this correction is an explicit new delta on that checkpoint.
