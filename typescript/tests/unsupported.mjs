import assert from 'node:assert/strict';
import { WorkbookClient } from '../dist/client.js';
import { JsonLineProcessTransport } from '../node-transport.mjs';
const command = JSON.parse(process.env.WORKBOOK_ENGINE_COMMAND);
const client = new WorkbookClient(new JsonLineProcessTransport(command[0], command.slice(1)));
try {
  const result = await client.recalculate(0, ['total']);
  assert(result.calculation.diagnostic_count > 0);
  assert(!('total' in result.calculation.outputs));
  const failed = result.observed_cells.find(x => x.address === 'B1');
  assert.equal(failed.value_origin, 'unavailable');
  assert.equal(failed.calculated_value, null);
  assert.equal(failed.content.cached_value, 777);
  await assert.rejects(client.export('unsupported.xlsx', 0), error => error.code === 'export_error');
  assert.equal(client.revision, 0);
  console.log(JSON.stringify({ ok: true, unavailable_is_not_cached: true, diagnostics: result.calculation.diagnostic_count }));
} finally { await client.close(); }
