// In-process engine test (no worker, no browser): node --test test/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createBlankWorkbook, initSync, openXlsx, replayJson, sha256Hex } from '../pkg/workbook_forge.js';
import { ForgeEngine } from '../src/engine.ts';

const wasmBytes = readFileSync(new URL('../pkg/workbook_forge_bg.wasm', import.meta.url));
initSync({ module: wasmBytes });
const fixture = readFileSync(new URL('../../../fixtures/univer-spike-chain.xlsx', import.meta.url));
let id = 0;
const call = (engine, command, args = {}) => engine.handle({ id: ++id, command, args }).result;

test('open, edit, calculate, conflict, export, reopen', () => {
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook });
  assert.equal(call(engine, 'snapshot').error.code, 'no_workbook');
  const opened = call(engine, 'open', { bytes: new Uint8Array(fixture) });
  assert.deepEqual(opened, { ok: true, value: { revision: '0', sheets: ['Sheet1'] } });
  const snap = call(engine, 'snapshot').value;
  assert.equal(snap.sheets[0].cells.C1.formula, 'B1+5');
  assert.equal(call(engine, 'calculate').value.values['Sheet1!F1'], 185);
  assert.deepEqual(call(engine, 'applyEdits', { edits: [{ sheet: 'Sheet1', address: 'A1', value: 20 }], expectedRevision: '0' }),
    { ok: true, value: { revision: '1' } });
  const stale = call(engine, 'applyEdits', { edits: [{ sheet: 'Sheet1', address: 'A1', value: 1 }], expectedRevision: '0' });
  assert.equal(stale.error.code, 'revision_conflict');
  assert.equal(stale.error.current_revision, '1');
  const absent = call(engine, 'applyEdits', { edits: [{ sheet: 'Sheet1', address: 'Z99', value: 1 }], expectedRevision: '1' });
  assert.equal(absent.error.code, 'absent_cell');
  assert.equal(call(engine, 'applyEdits', { edits: [{ sheet: 'Sheet1', address: 'C1', formula: 'B1+100' }], expectedRevision: '1' }).value.revision, '2');
  const calc = call(engine, 'calculate').value;
  assert.equal(calc.revision, '2');
  assert.equal(calc.values['Sheet1!F1'], 1000); // 20+40+140+420+380
  const bytes = call(engine, 'export').value.bytes;
  const reopened = new ForgeEngine({ openXlsx, createBlankWorkbook });
  call(reopened, 'open', { bytes });
  assert.equal(call(reopened, 'snapshot').value.sheets[0].cells.C1.formula, 'B1+100');
  assert.equal(call(reopened, 'calculate').value.values['Sheet1!F1'], 1000);
});

test('create blank, author new cells, calculate Excel formulas, export and reopen', () => {
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook });
  const created = call(engine, 'newWorkbook', { sheetName: 'Formula Lab' });
  assert.deepEqual(created, { ok: true, value: { revision: '0', sheets: ['Formula Lab'] } });

  const applied = call(engine, 'applyEdits', {
    edits: [
      { sheet: 'Formula Lab', address: 'A1', value: 2 },
      { sheet: 'Formula Lab', address: 'A2', value: 3 },
      { sheet: 'Formula Lab', address: 'A3', formula: 'SUM(A1:A2)' },
      { sheet: 'Formula Lab', address: 'B1', formula: 'IF(A3=5,"pass","fail")' },
    ],
    expectedRevision: '0',
  });
  assert.deepEqual(applied, { ok: true, value: { revision: '1' } });
  const stale = call(engine, 'applyEdits', {
    edits: [{ sheet: 'Formula Lab', address: 'A1', value: 4 }],
    expectedRevision: '0',
  });
  assert.equal(stale.error.code, 'revision_conflict');
  assert.equal(stale.error.expected_revision, '0');
  assert.equal(stale.error.current_revision, '1');
  const calc = call(engine, 'calculate');
  assert.equal(calc.value.values['Formula Lab!A3'], 5);
  assert.equal(calc.value.values['Formula Lab!B1'], 'pass');

  const bytes = call(engine, 'export').value.bytes;
  const reopened = new ForgeEngine({ openXlsx, createBlankWorkbook });
  call(reopened, 'open', { bytes });
  assert.equal(call(reopened, 'calculate').value.values['Formula Lab!A3'], 5);
  assert.equal(call(reopened, 'calculate').value.values['Formula Lab!B1'], 'pass');
});

test('the headless package imports no UI framework', async () => {
  const { readdirSync, readFileSync: read } = await import('node:fs');
  const dir = new URL('../src/', import.meta.url);
  for (const name of readdirSync(dir)) {
    const text = read(new URL(name, dir), 'utf8');
    assert.doesNotMatch(text, /from\s+['"](@univerjs|react|react-dom|rxjs)/, name);
  }
});

const envelope = (command_id, base_revision, edits, extra = {}) => ({
  v: 1,
  command_id,
  client_id: 'node-test',
  client_seq: '1',
  idempotency_key: null,
  actor: { kind: 'human', id: 'node-test', on_behalf_of: null },
  base_revision,
  mode: 'strict',
  preconditions: [],
  op: { type: 'set_cells', edits },
  ...extra,
});
const byKey = (changed) => Object.fromEntries(changed.map((c) => [`${c.sheet}!${c.address}`, c.value]));

test('applyAndCalculate answers with a delta, dedupes, fails closed, and queues a patch', () => {
  const engine = new ForgeEngine({ openXlsx });
  call(engine, 'open', { bytes: new Uint8Array(fixture) });
  const before = call(engine, 'calculate').value.values;
  const first = call(engine, 'applyAndCalculate', { envelope: envelope('cmd-1', '0', [{ sheet: 'Sheet1', address: 'A1', value: 20 }]) });
  assert.equal(first.ok, true);
  assert.equal(first.value.status, 'applied');
  assert.equal(first.value.revision, '1');
  assert.equal(first.value.previous_revision, '0');
  // A1=20: B1=40, C1=45, D1=135, E1=95, F1=20+40+45+135+95=335.
  assert.deepEqual(byKey(first.value.changed), {
    'Sheet1!A1': 20, 'Sheet1!B1': 40, 'Sheet1!C1': 45, 'Sheet1!D1': 135, 'Sheet1!E1': 95, 'Sheet1!F1': 335,
  });
  // changed == diff of two full calculations.
  const after = call(engine, 'calculate').value.values;
  const diff = Object.fromEntries(Object.entries(after).filter(([k, v]) => JSON.stringify(before[k]) !== JSON.stringify(v)));
  assert.deepEqual(byKey(first.value.changed), diff);
  assert.equal(first.value.changed.find((c) => c.address === 'C1').formula, 'B1+5');
  const patches = engine.drainPatches();
  assert.equal(patches.length, 1);
  assert.equal(patches[0].type, 'patch');
  assert.equal(patches[0].command_id, 'cmd-1');
  assert.equal(patches[0].revision, '1');
  assert.deepEqual(patches[0].changed, first.value.changed);

  const dup = call(engine, 'applyAndCalculate', { envelope: envelope('cmd-1', '0', [{ sheet: 'Sheet1', address: 'A1', value: 20 }]) });
  assert.equal(dup.value.duplicate, true);
  assert.deepEqual({ ...dup.value, duplicate: false }, first.value);
  assert.equal(engine.drainPatches().length, 0);

  const stale = call(engine, 'applyAndCalculate', { envelope: envelope('cmd-2', '0', [{ sheet: 'Sheet1', address: 'A1', value: 1 }]) });
  assert.equal(stale.ok, false);
  assert.equal(stale.error.code, 'revision_conflict');
  assert.equal(stale.error.current_revision, '1');
  const rebase = call(engine, 'applyAndCalculate', { envelope: envelope('cmd-3', '1', [{ sheet: 'Sheet1', address: 'A1', value: 1 }], { mode: 'rebase' }) });
  assert.equal(rebase.error.code, 'unsupported_mode');
  const bad = call(engine, 'applyAndCalculate', { envelope: envelope('cmd-4', '1', [], { v: 2 }) });
  assert.equal(bad.error.code, 'invalid_request');
  assert.equal(engine.drainPatches().length, 0);

  // Formula edit through the envelope; the legacy APIs still work alongside.
  const formula = call(engine, 'applyAndCalculate', { envelope: envelope('cmd-5', '1', [{ sheet: 'Sheet1', address: 'C1', formula: 'B1+100' }]) });
  assert.equal(formula.value.revision, '2');
  assert.equal(byKey(formula.value.changed)['Sheet1!F1'], 1000); // 20+40+140+420+380
  assert.equal(call(engine, 'applyEdits', { edits: [{ sheet: 'Sheet1', address: 'A1', value: 10 }], expectedRevision: '2' }).value.revision, '3');
  const next = call(engine, 'applyAndCalculate', { envelope: envelope('cmd-6', '3', [{ sheet: 'Sheet1', address: 'A1', value: 20 }]) });
  assert.equal(byKey(next.value.changed)['Sheet1!F1'], 1000);
  assert.equal(call(engine, 'calculate').value.values['Sheet1!F1'], 1000);
});

// Event log v1 on fixtures/realtime-chain.xlsx (A1 = 10, B1 = 0 literal,
// C1 = B1+5, D1 = C1*3, E1 = D1-B1, F1 = SUM(A1:E1)). Independent arithmetic:
//   B1 = A1*2 (A1 = 10): B1..F1 = 20, 25, 75, 55, 185
//   A1 = 20:             40, 45, 135, 95, 335
const realtime = readFileSync(new URL('../../../fixtures/realtime-chain.xlsx', import.meta.url));
const human = (id, base, op) => ({ ...envelope(id, base, []), actor: { kind: 'human', id: 'ui', on_behalf_of: null }, op });
const agent = (id, base, op) => ({ ...envelope(id, base, []), actor: { kind: 'agent', id: 'mcp:test', on_behalf_of: null }, op });
const setCells = (...edits) => ({ type: 'set_cells', edits: edits.map((e) => ({ sheet: 'Sheet1', ...e })) });

test('event log: intents, rejected intents, calc deltas, undo as an intent, export, and replay', () => {
  const wasmSha256 = sha256Hex(new Uint8Array(wasmBytes));
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook, sha256Hex, replayJson }, { wasmSha256 });
  call(engine, 'open', { bytes: new Uint8Array(realtime), name: 'realtime-chain.xlsx' });
  assert.deepEqual(call(engine, 'subscribe', { events: true }), { ok: true, value: { events: true } });
  const editor = { source: 'editor', univer: 'sheet.command.set-range-values' };

  const b1 = call(engine, 'applyAndCalculate', { envelope: human('c1', '0', setCells({ address: 'B1', formula: 'A1*2' })), command: editor });
  assert.equal(b1.value.revision, '1');
  assert.equal(b1.value.event_id, 'ev_0001');
  const a1 = call(engine, 'applyAndCalculate', { envelope: human('c2', '1', setCells({ address: 'A1', value: 20 })), command: editor });
  assert.equal(a1.value.revision, '2');
  const stale = call(engine, 'applyAndCalculate', { envelope: agent('c3', '1', setCells({ address: 'C1', value: 1 })) });
  assert.equal(stale.ok, false);
  assert.equal(stale.error.code, 'revision_conflict');
  assert.equal(stale.error.event_id, 'ev_0005');
  const undo = call(engine, 'applyAndCalculate', { envelope: human('u1', '2', { type: 'undo', target: null }), command: { source: 'history' } });
  assert.equal(undo.value.revision, '3');
  assert.equal(byKey(undo.value.changed)['Sheet1!B1'], 20);

  const { events, nextSeq, total } = call(engine, 'log', { fromSeq: 0 }).value;
  assert.equal(nextSeq, events.length);
  assert.equal(total, events.length);
  assert.deepEqual(events.map((e) => e.seq), events.map((_, i) => i));
  assert.deepEqual(events.map((e) => `${e.kind}:${e.status ?? e.op}`), [
    'session:opened', 'intent:applied', 'calc:undefined', 'intent:applied', 'calc:undefined', 'intent:rejected', 'intent:applied', 'calc:undefined',
  ]);
  const [opened] = events;
  assert.equal(opened.baseline.name, 'realtime-chain.xlsx');
  assert.equal(opened.baseline.sha256, sha256Hex(new Uint8Array(realtime)));
  assert.equal(opened.baseline.bytes, realtime.length);
  assert.equal(opened.engine.wasm_sha256, wasmSha256);
  const intents = events.filter((e) => e.kind === 'intent');
  assert.deepEqual(intents.map((e) => [e.actor.type, e.command.name, e.status, e.revision]), [
    ['human', 'edits', 'applied', '1'], ['human', 'edits', 'applied', '2'], ['agent', 'edits', 'rejected', '2'], ['human', 'undo', 'applied', '3'],
  ]);
  assert.deepEqual(intents[0].inputs, [{ sheet: 'Sheet1', address: 'B1', before: { value: 0 }, after: { formula: 'A1*2' } }]);
  assert.equal(intents[0].command.source, 'editor');
  assert.equal(intents[2].error.code, 'revision_conflict');
  assert.equal(intents[3].command.target, intents[1].id);
  const calcs = events.filter((e) => e.kind === 'calc');
  assert.deepEqual(calcs.map((e) => e.deltas), [
    [['Sheet1!B1', 0, 20], ['Sheet1!C1', 5, 25], ['Sheet1!D1', 15, 75], ['Sheet1!E1', 15, 55], ['Sheet1!F1', 45, 185]],
    [['Sheet1!B1', 20, 40], ['Sheet1!C1', 25, 45], ['Sheet1!D1', 75, 135], ['Sheet1!E1', 55, 95], ['Sheet1!F1', 185, 335]],
    [['Sheet1!B1', 40, 20], ['Sheet1!C1', 45, 25], ['Sheet1!D1', 135, 75], ['Sheet1!E1', 95, 55], ['Sheet1!F1', 335, 185]],
  ]);
  // subscribe {events:true} streamed exactly the log.
  assert.deepEqual(engine.drainEvents().map((m) => m.event), events);

  const exported = call(engine, 'export', { includeLog: true }).value;
  assert.equal(exported.logName, 'realtime-chain.forge-log.ndjson');
  const lines = exported.log.trim().split('\n').map((line) => JSON.parse(line));
  assert.equal(lines.length, events.length + 1);
  assert.deepEqual(lines.slice(0, -1), events);
  assert.equal(lines.at(-1).op, 'exported');
  assert.equal(lines.at(-1).revision, '3');
  assert.equal(lines.at(-1).xlsx_sha256, sha256Hex(exported.bytes));

  // replay(fixture, exportedLog) reproduces the final snapshot and every delta.
  const replayed = call(engine, 'replay', { baseline: new Uint8Array(realtime), events: exported.log });
  assert.equal(replayed.ok, true, JSON.stringify(replayed.error));
  assert.equal(replayed.value.revision, '3');
  assert.equal(replayed.value.intentsApplied, 3);
  assert.deepEqual(replayed.value.snapshot.sheets, call(engine, 'snapshot').value.sheets);
  assert.deepEqual(replayed.value.values, call(engine, 'calculate').value.values);
  assert.deepEqual(replayed.value.calcEvents.map((c) => c.deltas), calcs.map((c) => c.deltas));
  assert.deepEqual(replayed.value.calcEvents.map((c) => c.caused_by), calcs.map((c) => c.caused_by));
  // A JSON array that went through JSON.parse/stringify replays the same.
  assert.deepEqual(call(engine, 'replay', { baseline: new Uint8Array(realtime), events: lines }).value.calcEvents, replayed.value.calcEvents);

  const tampered = structuredClone(lines);
  tampered.find((e) => e.kind === 'calc').deltas[0][2] = 21;
  const mismatch = call(engine, 'replay', { baseline: new Uint8Array(realtime), events: tampered });
  assert.equal(mismatch.error.code, 'replay_mismatch');
  assert.equal(mismatch.error.event, 'ev_0002');
  const otherBaseline = call(engine, 'replay', { baseline: new Uint8Array(fixture), events: exported.log });
  assert.equal(otherBaseline.error.code, 'baseline_mismatch');
});

test('event log: undo_conflict names the cells and the event that overwrote them', () => {
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook, sha256Hex, replayJson });
  call(engine, 'open', { bytes: new Uint8Array(realtime), name: 'realtime-chain.xlsx' });
  call(engine, 'applyAndCalculate', { envelope: human('h1', '0', setCells({ address: 'A1', value: 20 })) });
  const overwrite = call(engine, 'applyAndCalculate', { envelope: agent('a1', '1', setCells({ address: 'A1', value: 30 })) });
  const conflict = call(engine, 'applyAndCalculate', { envelope: human('hu', '2', { type: 'undo' }) });
  assert.equal(conflict.ok, false);
  assert.equal(conflict.error.code, 'undo_conflict');
  assert.equal(conflict.error.target, 'ev_0001');
  assert.deepEqual(conflict.error.overwritten_by, [overwrite.value.event_id]);
  assert.deepEqual(conflict.error.cells, [
    { sheet: 'Sheet1', address: 'A1', expected: { value: 20 }, current: { value: 30 }, overwritten_by: overwrite.value.event_id },
  ]);
  assert.equal(call(engine, 'snapshot').value.revision, '2');
  const nothing = call(engine, 'applyAndCalculate', { envelope: human('hr', '2', { type: 'redo' }) });
  assert.equal(nothing.error.code, 'nothing_to_redo');
});
