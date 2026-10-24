/** Actual backend flow. Host provides one command argument array, never a shell string. */
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { WorkbookClient, encodeRequest } from '../dist/client.js';
import { JsonLineProcessTransport } from '../node-transport.mjs';

const command = JSON.parse(process.env.WORKBOOK_ENGINE_COMMAND ?? 'null');
if (!Array.isArray(command) || !command.length || command.some(x => typeof x !== 'string')) throw new Error('Set WORKBOOK_ENGINE_COMMAND to a host-selected JSON argument array');
const requests = [], encodedRequests = [], localValidation = [];
const processTransport = new JsonLineProcessTransport(command[0], command.slice(1));
const transport = { async request(request) { requests.push(request); encodedRequests.push(encodeRequest(request)); return processTransport.request(request); }, async close() { await processTransport.close(); } };
const client = new WorkbookClient(transport, 'synthetic');
try {
  const discovery = await client.discover();
  assert.equal(discovery.profile, 'agent-workbook-v1');
  const description = await client.describe();
  assert.equal(client.revision, 0);
  const range = await client.read({ sheet: 'Calc', range: 'A1:B3', limit: 2, expected_revision: 0 });
  assert.equal(range.cells.length, 2);
  assert.equal(range.next_offset, 2);
  const before = await client.recalculate(0);
  assert.equal(before.calculation.outputs.total, 5);
  assert.equal(before.calculation.outputs.doubled, 10);
  assert.equal(before.calculation.outputs.error.error, '#DIV/0!');
  assert(before.observed_cells.length > 100);
  // Mutating a returned view cannot corrupt the SDK's comparison baseline.
  before.observed_cells[0].calculated_value = 'host-local-edit';
  assert.equal((await client.recalculate(0)).changed_cells.length, 0);
  const raced = await Promise.allSettled([client.edit([{ sheet: 'Calc', address: 'A1', value: 7 }], 0), client.edit([{ sheet: 'Calc', address: 'A1', value: 1000 }], 0)]);
  assert.equal(raced[0].status, 'fulfilled');
  assert.equal(raced[0].value.revision, 1);
  assert.equal(raced[1].status, 'rejected');
  assert.equal(raced[1].reason.code, 'revision_conflict');
  await assert.rejects(client.edit([{ sheet: 'Calc', address: 'A1', value: 100 }], 0), error => error.code === 'revision_conflict');
  await assert.rejects(client.recalculate(0), error => error.code === 'revision_conflict');
  // Caller-only failures must never strand previously accepted edits.
  const cycle = {}; cycle.self = cycle;
  const localCases = [["NaN", NaN], ["Infinity", Infinity], ["negative-Infinity", -Infinity], ["undefined", undefined], ["cycle", cycle]];
  for (const [label, value] of localCases) {
    const sent = requests.length;
    await assert.rejects(client.edit([{ sheet: 'Calc', address: 'A1', value }], 1), error => error.code === 'invalid_input' && !error.uncertain);
    assert.equal(requests.length, sent);
    assert.equal(client.revision, 1);
    localValidation.push({ label, code: 'invalid_input', uncertain: false, requests_sent: requests.length - sent, revision: client.revision });
    const healthy = await client.calculate();
    assert.equal(healthy.revision, 1);
    assert.equal(healthy.outputs.total, 10);
  }
  const after = await client.recalculate(1);
  assert.equal(after.calculation.outputs.total, 10);
  assert.equal(after.calculation.outputs.doubled, 20);
  assert.deepEqual(after.changed_cells.map(x => x.address).sort(), ['A1', 'B1', 'B2']);
  assert.equal(after.observed_cells.find(x => x.address === 'A3').calculated_value, '4');
  assert.equal(after.observed_cells.find(x => x.address === 'A4').calculated_value, false);
  assert.equal(after.observed_cells.find(x => x.address === 'A5').value_origin, 'blank');
  // Failure to publish an over-large observation must not advance its baseline.
  await assert.rejects(client.recalculate(1, undefined, 10), error => error.code === 'resource_limit');
  assert.equal((await client.recalculate(1)).changed_cells.length, 0);
  await client.edit([{ sheet: 'Calc', address: 'B2', formula: '=IF(A1>0,B1*3,0)' }], 1);
  const formula = await client.recalculate(2);
  assert.equal(formula.calculation.outputs.doubled, 30);
  assert.deepEqual(formula.changed_cells.map(x => x.address), ['B2']);
  await client.edit([{ sheet: 'Calc', address: 'A6', value: -0 }, { sheet: 'Calc', address: 'A4', value: 0 }], 2);
  const zero = await client.recalculate(3);
  assert(Object.is(zero.observed_cells.find(x => x.address === 'A6').calculated_value, -0));
  assert.deepEqual(zero.changed_cells.map(x => x.address).sort(), ['A4', 'A6']);
  const exported = await client.export('sdk-example.xlsx', 3);
  await assert.rejects(client.export('sdk-example.xlsx', 3), error => error.code === 'export_error');
  assert.equal(new Set(requests.map(x => x.id)).size, requests.length);
  assert(requests.some(x => x.operation === 'explain' && x.arguments.offset === 100));
  const receipt = { backend_command: command[0], source_kind: description.source.kind, protocol: discovery.profile, revision: client.revision, outputs: zero.calculation.outputs, changed_after_input: after.changed_cells.map(x => ({ sheet: x.sheet, address: x.address })), changed_after_formula: formula.changed_cells.map(x => x.address), negative_zero_observed: true, observed_cells: zero.observed_cells.length, requests, encoded_requests: encodedRequests, local_validation: localValidation, exported, backend_execution: 'real child process', excel_observed: false };
  if (process.env.WORKBOOK_SDK_RECEIPT) await writeFile(process.env.WORKBOOK_SDK_RECEIPT, JSON.stringify(receipt, null, 2), { flag: 'wx' });
  console.log(JSON.stringify({ ok: true, revision: receipt.revision, outputs: receipt.outputs, observed_cells: receipt.observed_cells, request_count: requests.length, exported }));
} finally { await client.close(); }
