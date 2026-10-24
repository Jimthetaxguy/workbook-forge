import test from 'node:test';
import assert from 'node:assert/strict';
import { WorkbookClient, encodeRequest, CLIENT_CAPABILITIES } from '../dist/client.js';
import { JsonLineProcessTransport } from '../node-transport.mjs';

function fake(result, mutate = x => x) {
  const calls = [];
  return { calls, async request(request) { calls.push(request); return mutate({ schema_version: 1, id: request.id, operation: request.operation, ok: true, revision: 0, result: typeof result === 'function' ? result(request) : result }); }, async close() {} };
}
const calculation = { revision: 0, outputs: { zero: -0, error: { error: '#REF!', message: '' } }, diagnostics: [], diagnostic_count: 0, diagnostics_truncated: false, evaluated_cell_count: 2, stale: false };

test('strict wire encoding preserves negative zero and refuses nonfinite/oversized payloads', () => {
  const request = { id: '1', operation: 'edit', arguments: { edits: [{ sheet: 'X', address: 'A1', value: -0 }], expected_revision: 0 } };
  assert(Object.is(JSON.parse(encodeRequest(request)).arguments.edits[0].value, -0));
  assert.throws(() => encodeRequest({ ...request, arguments: { x: NaN } }), /object/);
  assert.throws(() => encodeRequest({ ...request, arguments: { x: 'x'.repeat(1048576) } }), error => error.code === 'resource_limit');
});

test('typed values keep blank/false/zero/error separate; capability boundaries explicit', async () => {
  const client = new WorkbookClient(fake(calculation));
  const result = await client.calculate();
  assert(Object.is(result.outputs.zero, -0));
  assert.equal(result.outputs.error.error, '#REF!');
  assert.equal(CLIENT_CAPABILITIES.arrays, false);
  assert.equal(CLIENT_CAPABILITIES.cancellation, false);
  await client.close();
});

for (const [name, change] of [
  ['id', x => ({ ...x, id: 'other' })], ['version', x => ({ ...x, schema_version: 2 })],
  ['operation', x => ({ ...x, operation: 'edit' })], ['unsafe revision', x => ({ ...x, revision: 9007199254740992 })],
]) test(`refuses ${name} mismatch without retry`, async () => {
  const transport = fake(calculation, change), client = new WorkbookClient(transport);
  await assert.rejects(client.calculate(), error => error.code === 'protocol_error');
  await assert.rejects(client.calculate(), error => error.code === 'closed');
  assert.equal(transport.calls.length, 1);
  await client.close();
});

test('arrays are an explicit unsupported projection, not blank results', async () => {
  const client = new WorkbookClient(fake({ ...calculation, outputs: { spilled: { array: [[1, 2]] } } }));
  await assert.rejects(client.calculate(), error => error.code === 'unsupported_result');
  await client.close();
});

test('transport failure on mutation is uncertain and never retried', async () => {
  let calls = 0;
  const client = new WorkbookClient({ async request() { calls++; throw new Error('lost response'); }, async close() {} });
  await assert.rejects(client.edit([{ sheet: 'X', address: 'A1', value: 2 }], 0), error => error.code === 'transport_error' && error.uncertain);
  await assert.rejects(client.edit([{ sheet: 'X', address: 'A1', value: 3 }], 0), error => error.code === 'closed');
  assert.equal(calls, 1); await client.close();
});

test('safe revision bounds refuse a mutation before sending', async () => {
  const transport = fake({}), client = new WorkbookClient(transport);
  await assert.rejects(client.edit([{ sheet: 'X', address: 'A1', value: 2 }], Number.MAX_SAFE_INTEGER), error => error.code === 'invalid_input');
  assert.equal(transport.calls.length, 0); await client.close();
});

test('owned child timeout terminates transport with no automatic restart', async () => {
  const transport = new JsonLineProcessTransport(process.execPath, ['-e', 'process.stdin.resume()'], { timeoutMs: 40 });
  const client = new WorkbookClient(transport);
  await assert.rejects(client.edit([{ sheet: 'X', address: 'A1', value: 2 }], 0), error => error.uncertain);
  await assert.rejects(client.calculate(), error => error.code === 'closed');
  await client.close();
});

test('owned child malformed UTF-8 is refused', async () => {
  const transport = new JsonLineProcessTransport(process.execPath, ['-e', 'process.stdin.once("data",()=>process.stdout.write(Buffer.from([255,10])))']);
  const client = new WorkbookClient(transport);
  await assert.rejects(client.calculate(), /Malformed/); await client.close();
});

test('calculation inner revision and oversized responses are refused', async () => {
  const mismatched = new WorkbookClient(fake({ ...calculation, revision: 1 }));
  await assert.rejects(mismatched.calculate(), error => error.code === 'protocol_error');
  await mismatched.close();
  const oversized = new WorkbookClient(fake({ ...calculation, diagnostics: [{ message: 'x'.repeat(262144) }] }));
  await assert.rejects(oversized.calculate(), /budget/); await oversized.close();
});

test('malformed mutation receipt is uncertain and blocks later requests', async () => {
  const transport = fake({ previous_revision: 0, revision: 2, changed_cells: [] }, x => ({ ...x, revision: 2 }));
  const client = new WorkbookClient(transport);
  await assert.rejects(client.edit([{ sheet: 'X', address: 'A1', value: 2 }], 0), error => error.uncertain && error.code === 'protocol_error');
  await assert.rejects(client.calculate(), error => error.code === 'closed');
  assert.equal(transport.calls.length, 1); await client.close();
});

test('unsafe read revision is local input failure; session remains usable', async () => {
  const transport = fake(calculation), client = new WorkbookClient(transport);
  await assert.rejects(client.read({ sheet: 'X', expected_revision: 9007199254740992 }), error => error.code === 'invalid_input');
  assert.equal(transport.calls.length, 0);
  await client.calculate(); await client.close();
});

for (const [label, value] of [['NaN', NaN], ['Infinity', Infinity], ['negative Infinity', -Infinity], ['undefined', undefined], ['function', () => 1], ['symbol', Symbol('invalid')]]) {
  test(`unsent local ${label} edit leaves healthy client usable`, async () => {
    const transport = fake(calculation), client = new WorkbookClient(transport);
    await client.calculate();
    const count = transport.calls.length, revision = client.revision;
    await assert.rejects(client.edit([{ sheet: 'X', address: 'A1', value }], revision), error => error.code === 'invalid_input' && !error.uncertain);
    assert.equal(transport.calls.length, count);
    assert.equal(client.revision, revision);
    await client.calculate();
    assert.equal(client.revision, revision);
    await client.close();
  });
}

test('cyclic/sparse local edits and invalid local read shape do not poison session', async () => {
  const transport = fake(calculation), client = new WorkbookClient(transport);
  await client.calculate();
  const cyclic = {}; cyclic.self = cyclic;
  for (const edits of [[{ sheet: 'X', address: 'A1', value: cyclic }], Array(1), undefined]) {
    const count = transport.calls.length;
    await assert.rejects(client.edit(edits, 0), error => error.code === 'invalid_input' && !error.uncertain);
    assert.equal(transport.calls.length, count);
    assert.equal(client.revision, 0);
  }
  const count = transport.calls.length;
  await assert.rejects(client.read({ sheet: undefined }), error => error.code === 'invalid_input' && !error.uncertain);
  assert.equal(transport.calls.length, count);
  await client.calculate(); await client.close();
});
