import { WorkbookClient, type Edit, type Transport } from '../dist/client.js';
import { JsonLineProcessTransport } from '../node-transport.mjs';
const transport: Transport = new JsonLineProcessTransport('host-selected', []);
const client = new WorkbookClient(transport);
const edits: Edit[] = [{ sheet: 'Calc', address: 'A1', value: 7 }, { sheet: 'Calc', address: 'B1', formula: '=A1*2' }];
void client.edit(edits, 0);
void client.recalculate(1, ['profit']);
// @ts-expect-error A value and formula cannot be submitted together.
const invalid: Edit = { sheet: 'Calc', address: 'A1', value: 1, formula: '=2' };
// @ts-expect-error A mutation requires an explicit expected revision.
void client.edit(edits);
// @ts-expect-error Arrays are outside the scalar client projection.
const arrayEdit: Edit = { sheet: 'Calc', address: 'A1', value: [1, 2] };
void invalid; void arrayEdit;
