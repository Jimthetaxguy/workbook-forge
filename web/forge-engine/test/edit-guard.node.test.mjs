// Non-finite edit values must not reach JSON.stringify: that call writes them
// as null, and Rust treats a null edit value as an explicit blank clear.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createBlankWorkbook, initSync, replayJson } from '../pkg/workbook_forge.js';
import { ForgeEngine } from '../src/engine.ts';

const wasmBytes = readFileSync(new URL('../pkg/workbook_forge_bg.wasm', import.meta.url));
initSync({ module: wasmBytes });

const NONFINITE = { ok: false, error: { code: 'invalid_request', message: 'numbers must be finite' } };
const NEG0 = '__NEG0__';

let id = 0;
const call = (engine, command, args = {}) => engine.handle({ id: ++id, command, args }).result;

/** The Rust edit object this engine builds before encoding. */
function rustEdits(edits) {
  return edits.map((edit) => {
    const out = { sheet: edit.sheet, address: edit.address };
    if (edit.formula !== undefined) out.formula = edit.formula.startsWith('=') ? edit.formula : `=${edit.formula}`;
    else out.value = Object.is(edit.value, -0) ? NEG0 : (edit.value ?? null);
    return out;
  });
}

/** JSON.stringify of the wire value, with negative zero restored as -0.0. */
function wireText(value) {
  return JSON.stringify(value).replaceAll(`"${NEG0}"`, '-0.0');
}

function recordingWasm() {
  const calls = [];
  const workbook = {
    snapshotJson: () => JSON.stringify({ sheets: [{ name: 'Sheet1', cells: {} }] }),
    revisionString: () => '0',
    applyEditsResultJson: (editsJson, expectedRevision) => {
      calls.push({ kind: 'applyEdits', json: editsJson, expectedRevision });
      return JSON.stringify({ ok: true, revision: '1' });
    },
    submitJson: (requestJson) => {
      calls.push({ kind: 'submit', json: requestJson });
      return JSON.stringify({
        v: 1,
        command_id: 'c',
        status: 'applied',
        revision: '1',
        previous_revision: '0',
        changed: [],
        evaluated_cells: 0,
        diagnostics: [],
        duplicate: false,
      });
    },
    startLog: () => {},
    logJson: () => JSON.stringify({ events: [], next_seq: 0, total: 0 }),
    exportLogNdjson: () => '',
    calculateJson: () => JSON.stringify({ revision: 0, values: {}, stale: false, diagnostics: [] }),
    exportXlsx: () => new Uint8Array(),
    free: () => {},
  };
  return {
    calls,
    engine: new ForgeEngine({
      createBlankWorkbook: () => workbook,
      replayJson: (_baseline, events) => {
        calls.push({ kind: 'replay', json: events });
        return JSON.stringify({ ok: false, error: { code: 'stopped', message: 'not a log' } });
      },
    }),
  };
}

const envelope = (commandId, edits, extra = {}) => ({
  v: 1,
  command_id: commandId,
  client_id: 'edit-guard',
  client_seq: '1',
  idempotency_key: null,
  actor: { kind: 'human', id: 'edit-guard', on_behalf_of: null },
  base_revision: '0',
  mode: 'strict',
  preconditions: [],
  op: { type: 'set_cells', edits },
  ...extra,
});

test('non-finite numbers are refused before the wasm call', () => {
  const { calls, engine } = recordingWasm();
  assert.equal(call(engine, 'newWorkbook', { sheetName: 'Sheet1' }).ok, true);
  const edits = [{ sheet: 'Sheet1', address: 'A1', value: 7 }];

  for (const value of [NaN, Infinity, -Infinity]) {
    assert.deepEqual(
      call(engine, 'applyEdits', { edits: [{ sheet: 'Sheet1', address: 'A1', value }], expectedRevision: '0' }),
      NONFINITE,
    );
    assert.deepEqual(
      call(engine, 'applyAndCalculate', { envelope: envelope('bad', [{ sheet: 'Sheet1', address: 'A1', value }]) }),
      NONFINITE,
    );
    assert.deepEqual(
      call(engine, 'replay', { baseline: new Uint8Array([1]), events: [{ after: { value } }] }),
      NONFINITE,
    );
  }
  // One non-finite value refuses the whole batch, including a following -0.
  assert.deepEqual(
    call(engine, 'applyEdits', {
      edits: [
        { sheet: 'Sheet1', address: 'A1', value: 1 },
        { sheet: 'Sheet1', address: 'A2', value: NaN },
      ],
      expectedRevision: '0',
    }),
    NONFINITE,
  );
  assert.deepEqual(calls, []);
  assert.equal(engine.drainPatches().length, 0);
  // The earlier finite shape is still accepted once the bad value is gone.
  assert.equal(call(engine, 'applyEdits', { edits, expectedRevision: '0' }).ok, true);
  assert.equal(calls.length, 1);
});

test('ordinary edit JSON is unchanged, and -0 is encoded as -0.0', () => {
  const { calls, engine } = recordingWasm();
  call(engine, 'newWorkbook', { sheetName: 'Sheet1' });
  const edits = [
    { sheet: 'Sheet "1"', address: 'A1', value: 0 },
    { sheet: 'Sheet "1"', address: 'A2', value: -2 },
    { sheet: 'Sheet "1"', address: 'A3', value: 1.5 },
    { sheet: 'Sheet "1"', address: 'A4', value: 1e21 },
    { sheet: 'Sheet "1"', address: 'A5', value: 'a\nb"c' },
    { sheet: 'Sheet "1"', address: 'A6', value: true },
    { sheet: 'Sheet "1"', address: 'A7', value: null },
    { sheet: 'Sheet "1"', address: 'A8', formula: 'SUM(A1)' },
    { sheet: 'Sheet "1"', address: 'A9', value: { error: '#N/A', message: 'missing' } },
  ];
  assert.equal(call(engine, 'applyEdits', { edits, expectedRevision: '0' }).ok, true);
  assert.equal(calls[0].json, wireText(rustEdits(edits)));
  assert.equal(calls[0].json, JSON.stringify(rustEdits(edits)));

  const signed = [
    { sheet: 'Sheet1', address: 'A1', value: 0 },
    { sheet: 'Sheet1', address: 'A2', value: -0 },
    { sheet: 'Sheet1', address: 'B1', formula: '=A1' },
  ];
  assert.equal(call(engine, 'applyEdits', { edits: signed, expectedRevision: '0' }).ok, true);
  assert.equal(calls[1].json, '[{"sheet":"Sheet1","address":"A1","value":0},{"sheet":"Sheet1","address":"A2","value":-0.0},{"sheet":"Sheet1","address":"B1","formula":"=A1"}]');
  assert.equal(calls[1].json, wireText(rustEdits(signed)));

  const command = { source: 'editor', univer: 'sheet.command.set-range-values' };
  const env = envelope('c-neg', [{ sheet: 'Sheet1', address: 'C1', value: -0 }]);
  assert.equal(call(engine, 'applyAndCalculate', { envelope: env, command }).ok, true);
  const wireOp = { ...env.op, edits: rustEdits(env.op.edits) };
  assert.equal(calls[2].json, wireText({ envelope: { ...env, op: wireOp }, command }));
  assert.match(calls[2].json, /"value":-0\.0/);

  const history = envelope('c-undo', [], { op: { type: 'undo', target: null } });
  assert.equal(call(engine, 'applyAndCalculate', { envelope: history }).ok, true);
  assert.equal(calls[3].json, JSON.stringify({ envelope: history }));

  const raw = '{"value":-0.0,"also":null}';
  call(engine, 'replay', { baseline: new Uint8Array([2]), events: raw });
  assert.equal(calls[4].json, raw);
  call(engine, 'replay', {
    baseline: new Uint8Array([3]),
    events: [{ sheet: 'Sheet1', address: 'A2', value: -0 }, { n: 0 }],
  });
  assert.equal(calls[5].json, wireText([{ sheet: 'Sheet1', address: 'A2', value: NEG0 }, { n: 0 }]));
});

function wrapWorkbook(wb, sent) {
  return {
    snapshotJson: () => wb.snapshotJson(),
    revisionString: () => wb.revisionString(),
    applyEditsResultJson: (editsJson, expectedRevision) => {
      sent.push({ kind: 'applyEdits', json: editsJson });
      return wb.applyEditsResultJson(editsJson, expectedRevision);
    },
    submitJson: (requestJson) => {
      sent.push({ kind: 'submit', json: requestJson });
      return wb.submitJson(requestJson);
    },
    startLog: (name, engineJson) => wb.startLog(name, engineJson),
    logJson: (fromSeq, limit) => wb.logJson(fromSeq, limit),
    exportLogNdjson: (xlsxSha256) => wb.exportLogNdjson(xlsxSha256),
    calculateJson: () => wb.calculateJson(),
    exportXlsx: () => wb.exportXlsx(),
    free: () => wb.free(),
  };
}

function cellValue(engine, address) {
  return call(engine, 'snapshot').value.sheets[0].cells[address]?.value;
}

test('wasm keeps finite edits, stores -0, and does not blank on non-finite input', () => {
  const sent = [];
  const engine = new ForgeEngine({
    createBlankWorkbook: (name) => wrapWorkbook(createBlankWorkbook(name), sent),
    replayJson: (baseline, events) => {
      sent.push({ kind: 'replay', json: events });
      return replayJson(baseline, events);
    },
  });
  assert.equal(call(engine, 'newWorkbook', { sheetName: 'Formula Lab' }).ok, true);
  const sheet = 'Formula Lab';
  const at = (address, value) => ({ sheet, address, value });

  const rev = () => call(engine, 'snapshot').value.revision;
  assert.equal(call(engine, 'applyEdits', { edits: [at('A1', 7)], expectedRevision: rev() }).value.revision, '1');
  const before = sent.length;
  for (const value of [NaN, Infinity, -Infinity]) {
    assert.deepEqual(
      call(engine, 'applyEdits', { edits: [at('A1', value)], expectedRevision: '1' }),
      NONFINITE,
    );
    assert.deepEqual(
      call(engine, 'applyAndCalculate', { envelope: envelope('bad-' + value, [at('A1', value)]) }),
      NONFINITE,
    );
  }
  const baseline = call(engine, 'export').value.bytes;
  assert.deepEqual(
    call(engine, 'replay', { baseline, events: [{ inputs: [{ after: { value: NaN } }] }] }),
    NONFINITE,
  );
  assert.equal(sent.length, before);
  assert.equal(call(engine, 'snapshot').value.revision, '1');
  assert.equal(cellValue(engine, 'A1'), 7);
  assert.equal(engine.drainPatches().length, 0);

  const signed = call(engine, 'applyEdits', {
    edits: [at('A1', 0), at('A2', -0)],
    expectedRevision: rev(),
  });
  assert.equal(signed.ok, true, JSON.stringify(signed.error));
  assert.equal(sent.at(-1).json, wireText(rustEdits([at('A1', 0), at('A2', -0)])));
  assert.equal(Object.is(cellValue(engine, 'A1'), 0), true);
  assert.equal(Object.is(cellValue(engine, 'A1'), -0), false);
  assert.equal(Object.is(cellValue(engine, 'A2'), -0), true);

  const calcNeg = call(engine, 'applyAndCalculate', {
    envelope: envelope('c-neg', [at('B1', -0)], { base_revision: rev() }),
  });
  assert.equal(calcNeg.ok, true, JSON.stringify(calcNeg.error));
  assert.match(sent.at(-1).json, /"value":-0\.0/);
  assert.equal(Object.is(cellValue(engine, 'B1'), -0), true);

  // 20 and A1+5 = 25 by arithmetic. A null value is still an explicit clear.
  assert.equal(call(engine, 'applyEdits', {
    edits: [at('A1', 20), { sheet, address: 'C1', formula: 'A1+5' }],
    expectedRevision: rev(),
  }).ok, true);
  assert.equal(cellValue(engine, 'A1'), 20);
  assert.equal(call(engine, 'calculate').value.values['Formula Lab!C1'], 25);
  assert.equal(call(engine, 'applyEdits', { edits: [at('A1', -1.5)], expectedRevision: rev() }).ok, true);
  assert.equal(cellValue(engine, 'A1'), -1.5);
  assert.equal(call(engine, 'applyEdits', { edits: [at('A2', 'ok')], expectedRevision: rev() }).ok, true);
  assert.equal(cellValue(engine, 'A2'), 'ok');
  assert.equal(call(engine, 'applyEdits', { edits: [at('A2', true)], expectedRevision: rev() }).ok, true);
  assert.equal(cellValue(engine, 'A2'), true);
  assert.equal(call(engine, 'applyEdits', { edits: [at('A2', null)], expectedRevision: rev() }).ok, true);
  assert.equal(cellValue(engine, 'A2'), null);
  assert.equal(sent.at(-1).json, JSON.stringify([{ sheet, address: 'A2', value: null }]));
});
