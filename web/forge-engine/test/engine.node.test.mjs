// In-process engine test (no worker, no browser): node --test test/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { copyFormulasJson, createBlankWorkbook, initSync, openXlsx, replayJson, sha256Hex } from '../pkg/workbook_forge.js';
import { ForgeEngine, utcOffsetMinutes } from '../src/engine.ts';

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
  const outside = call(engine, 'applyEdits', { edits: [{ sheet: 'Sheet1', address: 'XFE99', value: 1 }], expectedRevision: '1' });
  assert.equal(outside.error.code, 'out_of_bounds');
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

// Absent-cell authoring on an imported workbook (fixtures/univer-spike-chain.xlsx:
// A1 = 10 and formulas B1..F1; nothing below row 1). Independent arithmetic:
//   H1 = SUM(A1:A3) over A1 = 10 and blanks: 10
//   A2 = 5 (inserted inside that range):     H1 = 15
test('imported workbook: insert into absent cells, recalc the range, undo to absence, export and reopen', () => {
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook, sha256Hex, replayJson });
  call(engine, 'open', { bytes: new Uint8Array(fixture), name: 'univer-spike-chain.xlsx' });
  const sum = call(engine, 'applyAndCalculate', { envelope: human('a1', '0', setCells({ address: 'H1', formula: 'SUM(A1:A3)' })) });
  assert.equal(sum.value.status, 'applied');
  assert.equal(byKey(sum.value.changed)['Sheet1!H1'], 10);
  const typed = call(engine, 'applyAndCalculate', { envelope: human('a2', '1', setCells({ address: 'A2', value: 5 })) });
  assert.equal(typed.value.status, 'applied');
  assert.deepEqual(byKey(typed.value.changed), { 'Sheet1!A2': 5, 'Sheet1!H1': 15 });
  const log = call(engine, 'log', { fromSeq: 0, limit: 100 }).value.events;
  const intent = log.find((e) => e.id === typed.value.event_id);
  assert.deepEqual(intent.inputs, [{ sheet: 'Sheet1', address: 'A2', before: { absent: true }, after: { value: 5 } }]);
  const calc = log.find((e) => e.kind === 'calc' && e.caused_by[0] === typed.value.event_id);
  assert.deepEqual(calc.deltas, [['Sheet1!H1', 10, 15]]);

  // Undo removes A2 again (absent, not blank) and H1 repaints to 10.
  const undo = call(engine, 'applyAndCalculate', { envelope: human('u1', '2', { type: 'undo', target: null }) });
  assert.equal(undo.value.status, 'applied');
  assert.deepEqual(byKey(undo.value.changed), { 'Sheet1!A2': null, 'Sheet1!H1': 10 });
  assert.equal('A2' in call(engine, 'snapshot').value.sheets[0].cells, false);

  // Out-of-grid insertions are refused with a structured code.
  const outside = call(engine, 'applyAndCalculate', { envelope: human('o1', '3', setCells({ address: 'A1048577', value: 1 })) });
  assert.equal(outside.ok, false);
  assert.equal(outside.error.code, 'out_of_bounds');

  const redo = call(engine, 'applyAndCalculate', { envelope: human('r1', '3', { type: 'redo', target: null }) });
  assert.equal(byKey(redo.value.changed)['Sheet1!H1'], 15);
  const bytes = call(engine, 'export').value.bytes;
  const reopened = new ForgeEngine({ openXlsx, createBlankWorkbook });
  call(reopened, 'open', { bytes });
  const cells = call(reopened, 'snapshot').value.sheets[0].cells;
  assert.equal(cells.A2.value, 5);
  assert.equal(cells.H1.formula, 'SUM(A1:A3)');
  assert.equal(call(reopened, 'calculate').value.values['Sheet1!H1'], 15);
});

// Excel's lists, arrays in cells and nested arrays (Excel Beta, September
// 2026). The corpus files are modeled, not captured from Excel; Python and
// Rust hold them to the same codes and messages.
const corpusFile = (name) => readFileSync(new URL(`../../../fixtures/corpus/${name}`, import.meta.url));

test('a workbook holding an in-cell array is refused with its typed code and keeps the open workbook', () => {
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook });
  assert.equal(call(engine, 'open', { bytes: new Uint8Array(fixture) }).ok, true);
  for (const [name, sheet, address] of [
    ['hb_in_cell_list.xlsx', 'Projects', 'B2'],
    ['hb_in_cell_array_formula.xlsx', 'Splits', 'A1'],
    ['hb_nested_array.xlsx', 'Nested', 'A1'],
  ]) {
    const refused = call(engine, 'open', { bytes: new Uint8Array(corpusFile(name)) });
    assert.equal(refused.ok, false, name);
    assert.equal(refused.error.code, 'open_failed');
    assert.equal(refused.error.refusal, 'in_cell_array');
    assert.ok(refused.error.message.startsWith(`cell ${address} on sheet '${sheet}' holds an Excel array in a cell`), refused.error.message);
  }
  // The previous workbook stays open.
  assert.equal(call(engine, 'snapshot').value.sheets[0].name, 'Sheet1');
});

test('FLATTEN, HAS, HASANY and HASALL are typed, not calculated, and export is refused', () => {
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook });
  assert.equal(call(engine, 'open', { bytes: new Uint8Array(corpusFile('hb_flatten_has_functions.xlsx')) }).ok, true);
  const calc = call(engine, 'calculate').value;
  for (const [address, name] of [['A3', 'HAS'], ['B3', 'HASANY'], ['C3', 'HASALL'], ['D3', 'FLATTEN']]) {
    const diagnostic = calc.diagnostics.find((d) => d.address === address);
    assert.equal(diagnostic.code, 'unsupported_formula', address);
    assert.ok(diagnostic.message.endsWith(`in-cell array function ${name}: FLATTEN, HAS, HASANY and HASALL work on Excel arrays in cells and are not calculated`), diagnostic.message);
  }
  assert.equal(calc.values['Tags!E3'], 6);
  const typed = call(engine, 'applyEdits', { edits: [{ sheet: 'Tags', address: 'F3', formula: 'HAS(A1:C1,"drama")' }], expectedRevision: '0' });
  assert.equal(typed.ok, false);
  assert.equal(typed.error.code, 'unsupported_formula');
  // The browser session binds no outputs, so export refuses with a typed code
  // rather than write caches for formulas it did not calculate. Rust and
  // Python test the bound export, which preserves these cells byte-for-byte.
  const exported = call(engine, 'export');
  assert.equal(exported.ok, false);
  assert.equal(exported.error.code, 'export_failed');
  assert.equal(exported.error.refusal, 'calculation_diagnostics');
  assert.equal(call(engine, 'snapshot').value.sheets[0].cells.A3.formula, '_xlfn.HAS(A1:C1,"space")');
});

test('a Compatibility Version 3 workbook reads values and refuses formula edits with its code', () => {
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook });
  assert.equal(call(engine, 'open', { bytes: new Uint8Array(corpusFile('hb_compat_version_3.xlsx')) }).ok, true);
  assert.equal(call(engine, 'snapshot').value.sheets[0].cells.A2.value, 2);
  const refused = call(engine, 'applyEdits', { edits: [{ sheet: 'Calc', address: 'C1', formula: 'A2+1' }], expectedRevision: '0' });
  assert.equal(refused.ok, false);
  assert.equal(refused.error.code, 'compat_version_3');
  assert.equal(call(engine, 'applyEdits', { edits: [{ sheet: 'Calc', address: 'A2', value: 5 }], expectedRevision: '0' }).value.revision, '1');
  // No outputs are bound in the browser, so export refuses with the typed
  // code rather than write caches for formulas it did not calculate; the
  // formulas stay in the session unchanged. Rust and Python test the bound
  // export, which keeps them byte-for-byte.
  const exported = call(engine, 'export');
  assert.equal(exported.ok, false);
  assert.equal(exported.error.code, 'export_failed');
  assert.equal(exported.error.refusal, 'compat_version_3');
  assert.ok(exported.error.message.includes('Compatibility Version 3'), exported.error.message);
  assert.equal(call(engine, 'snapshot').value.sheets[0].cells.B1.formula, 'LEN(A1)');
});

test('NOW and TODAY follow the host time zone; calc events log the pass context', () => {
  // 2026-11-04T04:59:59Z is 23:59:59 on Nov 3 in New York (EST, UTC-05:00).
  assert.equal(utcOffsetMinutes('America/New_York', Date.UTC(2026, 10, 4, 4, 59, 59)), -300);
  assert.equal(utcOffsetMinutes('America/New_York', Date.UTC(2026, 6, 1, 12)), -240);
  assert.equal(utcOffsetMinutes('Asia/Kolkata', Date.UTC(2026, 0, 15, 12)), 330);
  assert.equal(utcOffsetMinutes('UTC', Date.UTC(2026, 0, 15, 12)), 0);
  assert.equal(utcOffsetMinutes('Not/AZone', Date.UTC(2026, 0, 15, 12)), 0);

  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook }, { timeZone: 'Asia/Kolkata' });
  call(engine, 'newWorkbook', { sheetName: 'Sheet1' });
  const envelope = {
    v: 1, command_id: 'now-1', client_id: 'c', client_seq: '1', idempotency_key: null,
    actor: { kind: 'human', id: 'ui', on_behalf_of: null }, base_revision: '0', mode: 'strict', preconditions: [],
    op: { type: 'set_cells', edits: [
      { sheet: 'Sheet1', address: 'A1', formula: 'NOW()' },
      { sheet: 'Sheet1', address: 'B1', formula: 'TODAY()' },
      { sheet: 'Sheet1', address: 'C1', formula: 'A1-INT(A1)' },
    ] },
  };
  const before = Date.now();
  assert.equal(call(engine, 'applyAndCalculate', { envelope }).ok, true);
  const after = Date.now();
  const calculated = call(engine, 'calculate').value;
  const values = calculated.values;
  const serial = (ms) => 25569 + (ms + 330 * 60000) / 86400000;
  assert.ok(values['Sheet1!A1'] >= serial(before - 1) && values['Sheet1!A1'] <= serial(after + 1000));
  assert.equal(values['Sheet1!B1'], Math.floor(values['Sheet1!A1']));
  assert.equal(calculated.calc.utc_offset_minutes, 330);
  assert.equal(typeof calculated.calc.now_ms, 'number');
  assert.match(calculated.calc.seed, /^\d+$/);
  const calc = call(engine, 'log', { fromSeq: 0, limit: 100 }).value.events.find((event) => event.kind === 'calc');
  assert.equal(calc.calc_context.utc_offset_minutes, 330);
  assert.equal(typeof calc.calc_context.seed, 'string');
});

test('replay of NOW()+RAND() returns the logged pass under a non-UTC zone', () => {
  const engine = new ForgeEngine(
    { openXlsx, createBlankWorkbook, sha256Hex, replayJson },
    { timeZone: 'America/New_York' },
  );
  call(engine, 'newWorkbook', { sheetName: 'Sheet1' });
  const envelope = {
    v: 1, command_id: 'volatile-1', client_id: 'c', client_seq: '1', idempotency_key: null,
    actor: { kind: 'human', id: 'ui', on_behalf_of: null }, base_revision: '0', mode: 'strict', preconditions: [],
    op: { type: 'set_cells', edits: [{ sheet: 'Sheet1', address: 'A1', formula: 'NOW()+RAND()' }] },
  };
  assert.equal(call(engine, 'applyAndCalculate', { envelope }).ok, true);
  const events = call(engine, 'log', { fromSeq: 0, limit: 100 }).value.events;
  const calc = events.find((event) => event.kind === 'calc');
  const offset = utcOffsetMinutes('America/New_York', calc.calc_context.now_ms);
  assert.notEqual(offset, 0);
  assert.equal(calc.calc_context.utc_offset_minutes, offset);
  assert.match(calc.calc_context.seed, /^\d+$/);
  const logged = calc.deltas.find((row) => row[0] === 'Sheet1!A1')[2];
  const exported = call(engine, 'export', { includeLog: true }).value;
  const replayed = call(engine, 'replay', { baseline: new Uint8Array(), events: exported.log });
  assert.equal(replayed.ok, true, JSON.stringify(replayed.error));
  assert.equal(replayed.value.values['Sheet1!A1'], logged);
  const again = call(engine, 'replay', { baseline: new Uint8Array(), events: exported.log });
  assert.equal(again.value.values['Sheet1!A1'], logged);
  const painted = call(engine, 'calculate').value;
  assert.equal(painted.calc.utc_offset_minutes, utcOffsetMinutes('America/New_York', painted.calc.now_ms));
  assert.equal(typeof painted.calc.now_ms, 'number');
  assert.match(painted.calc.seed, /^\d+$/);
});

// Presentation is read from the workbook's own XML, not from the engine:
// fixtures/corpus/op_styles_number_formats.xlsx sets <col width="18" min="1">
// and <col width="22" min="2">; A1 uses a bold font colored FF1F4E79; B1 uses
// numFmt "yyyy-mm-dd", solid fill FFFFF2CC and horizontal="right"; B4 uses
// built-in numFmtId 10, which ECMA-376 18.8.30 defines as 0.00%. General
// (numFmtId 0) is the default and is left out.
test('snapshot carries cell presentation and column widths', () => {
  const styled = readFileSync(new URL('../../../fixtures/corpus/op_styles_number_formats.xlsx', import.meta.url));
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook });
  assert.equal(call(engine, 'open', { bytes: new Uint8Array(styled) }).ok, true);
  const sheet = call(engine, 'snapshot').value.sheets[0];
  assert.deepEqual(sheet.columnWidths, { A: 18, B: 22 });
  assert.deepEqual(sheet.cells.A1, { value: 'date', style: { bold: true, fontColor: 'FF1F4E79' } });
  assert.deepEqual(sheet.cells.B1, {
    value: 45351,
    style: { numberFormat: 'yyyy-mm-dd', fillColor: 'FFFFF2CC', horizontal: 'right' },
  });
  assert.equal(sheet.cells.B4.style.numberFormat, '0.00%');
  assert.equal(sheet.cells.B4.value, 0.1275);

  // A workbook with no presentation carries neither field.
  assert.equal(call(engine, 'open', { bytes: new Uint8Array(fixture) }).ok, true);
  const plain = call(engine, 'snapshot').value.sheets[0];
  assert.equal('columnWidths' in plain, false);
  assert.deepEqual(plain.cells.A1, { value: 10 });
});

// The date system is read from the workbook's own XML, not from the engine:
// fixtures/corpus/op_1904_dates.xlsx and xw_1904_dates.xlsx carry
// <workbookPr date1904="1">, op_styles_number_formats.xlsx carries
// <workbookPr /> (ECMA-376 18.2.28: date1904 defaults to false), and a blank
// workbook is written with date1904="0". The stored serial is not changed.
test('snapshot says when a workbook uses the 1904 date system, and only then', () => {
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook });
  for (const [name, sheet, serial] of [['op_1904_dates.xlsx', 'Log', 42886], ['xw_1904_dates.xlsx', 'Calendar', 43236]]) {
    assert.equal(call(engine, 'open', { bytes: new Uint8Array(corpusFile(name)) }).ok, true);
    const snap = call(engine, 'snapshot').value;
    assert.equal(snap.date1904, true, name);
    assert.equal(snap.sheets[0].name, sheet);
    assert.deepEqual(snap.sheets[0].cells.A1, { value: serial, style: { numberFormat: 'yyyy-mm-dd' } });
  }
  assert.equal(call(engine, 'open', { bytes: new Uint8Array(corpusFile('op_styles_number_formats.xlsx')) }).ok, true);
  assert.equal('date1904' in call(engine, 'snapshot').value, false);
  assert.equal(call(engine, 'newWorkbook', { sheetName: 'Sheet1' }).ok, true);
  assert.equal('date1904' in call(engine, 'snapshot').value, false);
  // An engine build without the export (an older build, a mock) reports nothing.
  const older = new ForgeEngine({
    openXlsx: (bytes) => {
      const wb = openXlsx(bytes);
      return new Proxy(wb, { get: (target, key) => (key === 'usesDate1904' ? undefined : typeof target[key] === 'function' ? target[key].bind(target) : target[key]) });
    },
    createBlankWorkbook,
  });
  assert.equal(call(older, 'open', { bytes: new Uint8Array(corpusFile('op_1904_dates.xlsx')) }).ok, true);
  assert.equal('date1904' in call(older, 'snapshot').value, false);
});

// copyFormulas: what a formula becomes when its cell is copied elsewhere.
// Expected references are Microsoft's, from "Switch between relative,
// absolute, and mixed references" (support.microsoft.com, article
// dfec08cd-ae65-4f56-839e-5f0d8d0baca9): copied two cells down and two cells
// to the right, $A$1 stays $A$1, A$1 becomes C$1, $A1 becomes $A3 and A1
// becomes C3; and =B4*C4 copied from D4 to D5 becomes =B5*C5 while
// =$B$4*$C$4 stays the same.
test('copyFormulas moves relative references, keeps absolute ones, and reads no workbook', () => {
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook, copyFormulasJson });
  // No workbook is open: the command is stateless.
  const copied = call(engine, 'copyFormulas', {
    items: [
      { formula: '$A$1', rowDelta: 2, columnDelta: 2 },
      { formula: 'A$1', rowDelta: 2, columnDelta: 2 },
      { formula: '$A1', rowDelta: 2, columnDelta: 2 },
      { formula: 'A1', rowDelta: 2, columnDelta: 2 },
      { formula: 'B4*C4', rowDelta: 1, columnDelta: 0 },
      { formula: '=$B$4*$C$4', rowDelta: 1, columnDelta: 0 },
      { formula: 'A1', rowDelta: -1, columnDelta: 0 },
      { formula: 'B2', rowDelta: 0, columnDelta: -2 },
      { formula: '1+', rowDelta: 1, columnDelta: 0 },
    ],
  });
  assert.equal(copied.ok, true, JSON.stringify(copied.error));
  const formulas = copied.value.formulas;
  assert.deepEqual(formulas.slice(0, 6), [
    { formula: '$A$1', spelling: 'kept' },
    { formula: 'C$1', spelling: 'kept' },
    { formula: '$A3', spelling: 'kept' },
    { formula: 'C3', spelling: 'kept' },
    { formula: 'B5*C5', spelling: 'kept' },
    { formula: '$B$4*$C$4', spelling: 'kept' },
  ]);
  // Row 0 and the column left of A do not exist: each is refused on its own.
  assert.equal(formulas[6].error.code, 'invalid_reference');
  assert.equal(formulas[7].error.code, 'invalid_reference');
  assert.equal(formulas[8].error.code, 'parse_error');
  assert.equal(call(engine, 'snapshot').error.code, 'no_workbook');
});

// The copy keeps the formula as it was typed whenever the toolkit reads the
// moved text back as the same formula as its own copy. These spellings are
// what Excel shows for a copied formula: it moves the references and leaves
// the rest alone (same Microsoft article: =B4*C4 becomes =B5*C5).
test('copyFormulas keeps the typed spelling when the toolkit confirms it', () => {
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook, copyFormulasJson });
  const copy = (formula, rowDelta, columnDelta) => call(engine, 'copyFormulas', { items: [{ formula, rowDelta, columnDelta }] }).value.formulas[0];
  const kept = (formula) => ({ formula, spelling: 'kept' });
  assert.deepEqual(copy('A1+B1', 1, 0), kept('A2+B2'));
  assert.deepEqual(copy('SUM(A1:A3)*10%', 0, 1), kept('SUM(B1:B3)*10%'));
  assert.deepEqual(copy('sum(a1:a3)', 1, 0), kept('sum(A2:A4)'));
  // Text that looks like a cell stays text; a sheet prefix stays as typed.
  assert.deepEqual(copy('IF(A1>0,"A1 ok",Sheet2!B2)', 1, 1), kept('IF(B2>0,"A1 ok",Sheet2!C3)'));
  assert.deepEqual(copy("'My Sheet'!A1&\"x\"", 2, 0), kept("'My Sheet'!A3&\"x\""));
  // A function whose name ends like a cell is not a reference.
  assert.deepEqual(copy('LOG10(A1)', 1, 0), kept('LOG10(A2)'));
  assert.deepEqual(copy('$A1+A$1', 2, 2), kept('$A3+C$1'));
  // Not moved at all: returned exactly as given.
  assert.deepEqual(copy('A1 + B1', 0, 0), kept('A1 + B1'));
  // A number in scientific notation is not column E.
  assert.deepEqual(copy('1E5+A1', 1, 0), kept('1E5+A2'));
});

test('copyFormulas falls back to the toolkit spelling when the typed one is not confirmed', async () => {
  const { shiftReferences } = await import('../src/reference-shift.ts');
  // The text pass on its own.
  assert.equal(shiftReferences('A1+$B$2', 1, 1), 'B2+$B$2');
  assert.equal(shiftReferences('"A1"&A1', 1, 0), '"A1"&A2');
  assert.equal(shiftReferences('Q1!A1+LOG10(A1)', 0, 1), 'Q1!B1+LOG10(B1)');
  assert.equal(shiftReferences('A1', -1, 0), null);
  assert.equal(shiftReferences('XFD1', 0, 1), null);
  assert.equal(shiftReferences('A1048576', 1, 0), null);
  // A stand-in toolkit whose copy disagrees with the text pass: its spelling wins.
  const fake = {
    openXlsx,
    createBlankWorkbook,
    copyFormulasJson: (json) => {
      const items = JSON.parse(json);
      return JSON.stringify({ ok: true, formulas: items.map((item) => ({ formula: item.row_delta === 0 ? `read(${item.formula})` : '(TOOLKIT)' })) });
    },
  };
  const engine = new ForgeEngine(fake);
  const out = call(engine, 'copyFormulas', { items: [{ formula: 'A1+B1', rowDelta: 1, columnDelta: 0 }] });
  assert.deepEqual(out.value.formulas, [{ formula: '(TOOLKIT)', spelling: 'bracketed' }]);
  // The real toolkit cannot move a reference off the grid; the text pass gives up too.
  const real = new ForgeEngine({ openXlsx, createBlankWorkbook, copyFormulasJson });
  assert.equal(call(real, 'copyFormulas', { items: [{ formula: 'XFD1', rowDelta: 0, columnDelta: 1 }] }).value.formulas[0].error.code, 'invalid_reference');
});

test('copyFormulas results calculate as Excel\'s relative copy does, and nothing is logged', () => {
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook, copyFormulasJson });
  call(engine, 'newWorkbook', { sheetName: 'Sheet1' });
  const edits = [
    { sheet: 'Sheet1', address: 'A1', value: 1 },
    { sheet: 'Sheet1', address: 'A2', value: 2 },
    { sheet: 'Sheet1', address: 'A3', value: 3 },
    { sheet: 'Sheet1', address: 'B1', formula: 'A1*10' },
  ];
  assert.equal(call(engine, 'applyEdits', { edits, expectedRevision: '0' }).ok, true);
  const before = call(engine, 'log', { fromSeq: 0 }).value.total;
  const copied = call(engine, 'copyFormulas', {
    items: [
      { formula: 'A1*10', rowDelta: 1, columnDelta: 0 },
      { formula: 'A1*10', rowDelta: 2, columnDelta: 0 },
    ],
  }).value.formulas;
  assert.deepEqual(copied, [{ formula: 'A2*10', spelling: 'kept' }, { formula: 'A3*10', spelling: 'kept' }]);
  assert.equal(call(engine, 'log', { fromSeq: 0 }).value.total, before);
  assert.equal(call(engine, 'snapshot').value.revision, '1');
  const filled = [
    { sheet: 'Sheet1', address: 'B2', formula: copied[0].formula },
    { sheet: 'Sheet1', address: 'B3', formula: copied[1].formula },
  ];
  assert.equal(call(engine, 'applyEdits', { edits: filled, expectedRevision: '1' }).ok, true);
  const values = call(engine, 'calculate').value.values;
  // 1*10, 2*10, 3*10 by hand.
  assert.deepEqual([values['Sheet1!B1'], values['Sheet1!B2'], values['Sheet1!B3']], [10, 20, 30]);
});

test('copyFormulas is refused by an engine build without it and for malformed items', () => {
  const old = new ForgeEngine({ openXlsx, createBlankWorkbook });
  assert.equal(call(old, 'copyFormulas', { items: [] }).error.code, 'unsupported');
  const engine = new ForgeEngine({ openXlsx, createBlankWorkbook, copyFormulasJson });
  assert.equal(call(engine, 'copyFormulas', { items: 'A1' }).error.code, 'invalid_request');
  assert.equal(call(engine, 'copyFormulas', { items: [{ formula: 'A1', rowDelta: 1.5, columnDelta: 0 }] }).error.code, 'invalid_request');
  assert.deepEqual(call(engine, 'copyFormulas', { items: [] }), { ok: true, value: { formulas: [] } });
});
