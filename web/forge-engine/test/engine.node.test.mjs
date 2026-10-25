// In-process engine test (no worker, no browser): node --test test/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { initSync, openXlsx } from '../pkg/workbook_forge.js';
import { ForgeEngine } from '../src/engine.ts';

initSync({ module: readFileSync(new URL('../pkg/workbook_forge_bg.wasm', import.meta.url)) });
const fixture = readFileSync(new URL('../../../fixtures/univer-spike-chain.xlsx', import.meta.url));
let id = 0;
const call = (engine, command, args = {}) => engine.handle({ id: ++id, command, args }).result;

test('open, edit, calculate, conflict, export, reopen', () => {
  const engine = new ForgeEngine({ openXlsx });
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
  const reopened = new ForgeEngine({ openXlsx });
  call(reopened, 'open', { bytes });
  assert.equal(call(reopened, 'snapshot').value.sheets[0].cells.C1.formula, 'B1+100');
  assert.equal(call(reopened, 'calculate').value.values['Sheet1!F1'], 1000);
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
