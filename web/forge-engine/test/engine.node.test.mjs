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
