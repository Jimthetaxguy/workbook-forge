# Optional TypeScript workbook client

A transport-neutral typed client over the existing **agent-workbook-v1** protocol,
not a third evaluator or workbook model. The compiler CLI and Python/Rust engines
have no dependency on this package. No UI, server, Tauri or FastAPI is required.

The browser-compatible core has no Node imports; browser bundling/execution is
not yet exercised. The concrete adapter here uses Node child-process JSON-lines
and works with either existing independent backend runner. A future Tauri IPC or
HTTP/WebSocket host can implement `Transport`; those integrations, durable
sessions and cancellation are **not implemented**.

## Run locally without installing dependencies

Tested using the existing Node 22.22.3 and TypeScript 5.9.3 tools. From repository
root, with the existing Python development environment active:

```sh
tsc -p typescript/tsconfig.json
cd typescript && npm test
```

From repository root, build the native runner offline, then replay both real
engines on the same synthetic fixture (choose a new output directory):

```sh
CARGO_TARGET_DIR="$PWD/rust/target" cargo build --manifest-path rust/Cargo.toml --locked --offline --example agent_workbook
PYTHONPATH=python python tools/typescript_sdk_receipt.py .verification/ts-client-new --rust-binary rust/target/debug/examples/agent_workbook
```

This writes a fixture in the **existing** workbook JSON schema plus XLSX and
bindings, runs Python/Rust × authored/imported lanes through compiled TS, and
retains requests, exact encoded request lines, receipts and exported workbooks.
Final independent reimport uses Python, explicitly recorded in each lane.
The authored JSON is synthetic and contains deliberately misleading caches; the
XLSX fixture is initially calculated by the existing writer. Subsequent input and
formula edits, plus separate unsupported-formula probes, verify fresh results.
No network, dependency installation or Excel application is needed.

For one host-selected command, use `typescript/examples/synthetic.mjs` with
`WORKBOOK_ENGINE_COMMAND` set to a JSON string array, never a shell command.
The fixture builder is `examples/ts_client_fixture.py NEW_DIRECTORY`.

## Interface

```ts
import { WorkbookClient } from './dist/client.js';
import { JsonLineProcessTransport } from './node-transport.mjs';

// Host owns these paths and selects the workbook at process startup.
const transport = new JsonLineProcessTransport(pythonExecutable, [
  '-m', 'workbook_forge.cli', 'agent', workbookPath,
  '--bindings', bindingsPath, '--output-dir', exportDirectory,
]);
const client = new WorkbookClient(transport, 'webview-host');
try {
  const info = await client.describe();
  const revision = client.revision!;
  const page = await client.read({ sheet: 'Calc', range: 'A1:B3', expected_revision: revision });
  const before = await client.recalculate(revision);
  const edit = await client.edit([{ sheet: 'Calc', address: 'A1', value: 7 }], revision);
  const after = await client.recalculate(edit.revision);
  // after.changed_cells contains backend-calculated changes in observed closures.
  const receipt = await client.export('updated.xlsx', after.revision);
} finally {
  await client.close();
}
```

`discover`, `describe`, `read`, `edit`, `calculate`, `recalculate`, `export`, and
`close` are the client methods. `calculate` returns the existing compact named
output report. `recalculate` composes that operation with revision-pinned,
paginated `explain` calls to observe selected named output dependency closures.
It never evaluates formulas in TypeScript. Sheets/addresses are structured fields;
opaque backend sheet IDs are not used for cross-engine identity.

`read` returns populated authored cell records, including **separate** original
formulas and stored `cached_value`. It does not present caches as current results.
`recalculate` returns calculated observations with the existing `value_origin`
(calculated/authored/blank/unavailable). An unavailable null is distinguished from
a calculated/authored blank through that origin. Finite numbers, text, booleans,
blanks and Excel error objects stay typed; negative zero remains distinct at the
wire and change-comparison boundaries. Error messages are retained, but change
detection compares error codes rather than backend-specific wording.

## Scope and invariants

- Protocol version/limits/operation names are generated from
  `catalog/agent-operations.json` into `src/contract.ts`; run
  `python tools/generate_ts_contract.py --check`. Existing backend contracts are
  unchanged. Client type projections and runtime result checks live in
  `src/client.ts`, not a parallel schema for workbook persistence.
- One transient backend session per client. Request IDs are matched with operation
  and schema version. Client calls are serialized; independent host/client writes
  can still race, so revision checks remain authoritative. No automatic retries.
- Existing wire controls allow uint64. This JavaScript client deliberately refuses
  unsafe control integers above `Number.MAX_SAFE_INTEGER`; mutations require the
  next revision to fit too. Full uint64 controls are not silently rounded. Cell
  numbers use JavaScript/binary64; arbitrary exact large Python integers are not
  an additional client value type.
- Reads retain existing pagination/100-cell and response-byte bounds. Observed
  changes default to at most 1,000 unique cells, configurable up to 10,000, plus a
  1 MiB total observation projection and 200-page bound. No partial projection is
  published or saved as a comparison baseline after a failed collection.
- The first observation reports all cells as new. Subsequent diffs compare values
  and value origins only, for the same selected output names. Changing selection
  starts a new baseline. These are **observed closure changes**, not a complete
  engine push/change log, global dirty set, metadata diff or removed-cell event.
  Mutation receipts separately report edited fields. Returned views are detached
  from the internal observation baseline.
- Output bindings are required. Whole-sheet/range calculation, array/spill
  projections, arbitrary workbook loading through requests, cancellation and
  durable reconnect are explicit capability gaps. Read ranges are sparse
  populated cells, not a dense spreadsheet UI/layout model.
- `recalculate` currently repeats backend calculation through each explain page.
  It proves an interoperable foundation, not an optimized subscription pipeline
  or performance gain. A future atomic backend projection operation can reduce
  that cost; no new engine operation/schema is invented in this stage.
- A timeout, process failure or malformed mutation response can leave an uncertain
  outcome. The client closes its usable state and does not retry the mutation or
  export. The host must reconcile retained output files/session state; reopening
  a process is not a durable-session recovery protocol. No cleanup of existing
  exported files occurs. Source/workbook/export paths are host-selected only.

## Evidence

`tests/client.test.mjs` uses controlled fake responses for client validation and
owned Node children for timeout/malformed UTF-8 tests; those are not formula-engine
tests. `python/tests/test_typescript_sdk.py` runs the actual independent Python and
Rust runners for authored/XLSX editing, calculation, pagination, changed values,
typed errors, unsupported formulas, stale revisions, export and reimport.
`tests/typecheck.ts` checks public declarations and rejected API shapes.
No actual Tauri/webview, browser, FastAPI, HTTP/WebSocket, installed npm package or
fresh Excel recalculation/no-repair opening is claimed.

### Local rejection versus an uncertain backend outcome

Caller-side JSON normalization errors (nonfinite numbers, undefined/function/symbol
values, cycles/depth overflow and sparse edit arrays) return `invalid_input`
without sending a request or disabling the healthy client. The same applies to
invalid local read argument objects. Revision and observation state remain intact;
subsequent read/calculate/edit/export uses the existing session. Valid JSON with
an invalid backend schema still receives the backend's normal structured refusal.
Response conversion continues to use strict protocol errors. Once bytes may have
been sent, malformed responses or transport loss retain the existing fail-closed,
uncertain-mutation/no-retry behavior. The two paths do not share a blanket catch.
