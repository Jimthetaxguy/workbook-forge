/** Client projections of agent-workbook-v1; no workbook schema or evaluator. */
import { LIMITS, OPERATIONS, PROFILE, SCHEMA_VERSION } from './contract.js';
export { LIMITS, PROFILE, SCHEMA_VERSION } from './contract.js';
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type ExcelError = { error: string; message?: string };
export type Scalar = null | boolean | number | string | ExcelError;
export type Operation = typeof OPERATIONS[number];
export interface Address { sheet: string; address: string }
export interface Style { number_format?: string; bold?: boolean; font_color?: string; fill_color?: string; horizontal?: 'left' | 'center' | 'right' }
export type Edit = Address & ({ value: Scalar; formula?: never; style?: Style } | { formula: string; value?: never; style?: Style } | { style: Style; value?: never; formula?: never });
export interface Request { id: string; operation: Operation; arguments: { [key: string]: Json } }
export interface Transport { request(request: Request): Promise<unknown>; close(): Promise<void> }
export interface ReadOptions { sheet?: string; range?: string; output?: string; offset?: number; limit?: number; expected_revision?: number }
export interface Cell extends Address {
  sheet_id: string;
  content: { [key: string]: Json };
  dependencies: string[];
  dependency_count: number;
  dependencies_truncated: boolean;
}
export interface ObservedCell extends Cell { calculated_value: Scalar; value_origin: 'calculated' | 'authored' | 'blank' | 'unavailable' }
export interface Diagnostics { diagnostics: { [key: string]: Json }[]; diagnostic_count: number; diagnostics_truncated: boolean }
export interface Page extends Diagnostics { cells: Cell[]; offset: number; limit: number; total_cells: number; next_offset: number | null; truncated: boolean }
export interface Explanation extends Omit<Page, 'cells'> { cells: ObservedCell[]; output: string; binding: Address; value: Scalar }
export interface Calculation extends Diagnostics { revision: number; outputs: { [key: string]: Scalar }; evaluated_cell_count: number; stale: boolean }
export interface Description { sheets: { id: string; name: string; populated_cells: number }[]; inputs: { [key: string]: Json }; outputs: { [key: string]: Address }; limits: { [key: string]: Json }; source: { kind: 'authored' | 'xlsx' } }
export interface Mutation { previous_revision: number; revision: number; changed_cells: (Address & { fields: string[] })[] }
export interface ExportReceipt { filename: string; revision: number; bytes: number }
export interface CellChange extends Address { before: ObservedCell | null; after: ObservedCell }
export interface Recalculation { revision: number; calculation: Calculation; observed_cells: ObservedCell[]; changed_cells: CellChange[]; scope: 'selected-output-closures' }
export const CLIENT_CAPABILITIES = Object.freeze({ protocol: PROFILE, scalar_results: true, paged_range_reads: true, revision_checked_edits: true, observed_closure_changes: true, whole_sheet_calculation: false, arrays: false, cancellation: false, durable_sessions: false, transport: 'host-supplied' });

export class ClientError extends Error {
  constructor(public readonly code: string, message: string, public readonly uncertain = false) { super(message); this.name = 'ClientError'; }
}
function fail(message: string): never { throw new ClientError('protocol_error', message); }
function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) fail('Expected an object');
  return value as Record<string, unknown>;
}
function text(value: unknown): string { if (typeof value !== 'string' || !value.length) fail('Expected nonempty text'); return value; }
function bool(value: unknown): boolean { if (typeof value !== 'boolean') fail('Expected Boolean'); return value; }
function integer(value: unknown, minimum = 0): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) fail('Control integers must fit JavaScript safe integers');
  return value;
}
function array(value: unknown): unknown[] { if (!Array.isArray(value)) fail('Expected an array'); return value; }
function json(value: unknown, depth = 0): Json {
  if (depth > 64) fail('JSON nesting exceeds client limit');
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return Array.from(value, x => json(x, depth + 1));
  const out: { [key: string]: Json } = Object.create(null);
  for (const [key, item] of Object.entries(object(value))) out[key] = json(item, depth + 1);
  return out;
}
function jsonObject(value: unknown): { [key: string]: Json } { object(value); return json(value) as { [key: string]: Json }; }
/** Only caller-side conversion errors are local input failures, never wire failures. */
function inputJson(value: unknown): Json {
  try { return json(value); }
  catch (error) {
    if (error instanceof ClientError && error.code === 'protocol_error') throw new ClientError('invalid_input', error.message);
    throw error;
  }
}
function inputObject(value: unknown): { [key: string]: Json } {
  const result = inputJson(value);
  if (result === null || typeof result !== 'object' || Array.isArray(result)) throw new ClientError('invalid_input', 'Arguments must be an object');
  return result;
}

function scalar(value: unknown): Scalar {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const error = object(value);
  if (Object.keys(error).some(k => k !== 'error' && k !== 'message')) throw new ClientError('unsupported_result', 'Client scalar profile does not project arrays or unknown value objects');
  const result: ExcelError = { error: text(error.error) };
  if (error.message !== undefined) { if (typeof error.message !== "string") fail("Error message must be text"); result.message = error.message; }
  return result;
}
function address(value: unknown): Address { const x = object(value); return { sheet: text(x.sheet), address: text(x.address) }; }
function diagnostics(value: Record<string, unknown>): Diagnostics {
  return { diagnostics: array(value.diagnostics).map(jsonObject), diagnostic_count: integer(value.diagnostic_count), diagnostics_truncated: bool(value.diagnostics_truncated) };
}
function cell(value: unknown): Cell {
  const x = object(value), content = jsonObject(x.content);
  for (const key of ['value', 'cached_value']) if (key in content) scalar(content[key]);
  if ('formula' in content && content.formula !== null) text(content.formula);
  return { ...address(x), sheet_id: text(x.sheet_id), content, dependencies: array(x.dependencies).map(text), dependency_count: integer(x.dependency_count), dependencies_truncated: bool(x.dependencies_truncated) };
}
function observed(value: unknown): ObservedCell {
  const x = object(value), origin = text(x.value_origin);
  if (!['calculated', 'authored', 'blank', 'unavailable'].includes(origin)) fail('Unknown value origin');
  return { ...cell(x), calculated_value: scalar(x.calculated_value), value_origin: origin as ObservedCell['value_origin'] };
}
function page(value: unknown): Page {
  const x = object(value), cells = array(x.cells).map(cell), offset = integer(x.offset), limit = integer(x.limit, 1), total = integer(x.total_cells);
  const next = x.next_offset === null ? null : integer(x.next_offset, 1);
  if (limit > LIMITS.max_page_cells || cells.length > limit || next !== null && next !== offset + cells.length || bool(x.truncated) !== (next !== null)) fail('Inconsistent page bounds');
  return { ...diagnostics(x), cells, offset, limit, total_cells: total, next_offset: next, truncated: next !== null };
}
function calculation(value: unknown): Calculation {
  const x = object(value), outputs: { [key: string]: Scalar } = Object.create(null);
  for (const [key, item] of Object.entries(object(x.outputs))) outputs[key] = scalar(item);
  return { ...diagnostics(x), revision: integer(x.revision), outputs, evaluated_cell_count: integer(x.evaluated_cell_count), stale: bool(x.stale) };
}
function description(value: unknown): Description {
  const x = object(value), outputs: { [key: string]: Address } = Object.create(null), source = object(x.source);
  for (const [key, item] of Object.entries(object(x.outputs))) outputs[key] = address(item);
  if (source.kind !== 'authored' && source.kind !== 'xlsx') fail('Unknown workbook source kind');
  return { sheets: array(x.sheets).map(item => { const s = object(item); return { id: text(s.id), name: text(s.name), populated_cells: integer(s.populated_cells) }; }), inputs: jsonObject(x.inputs), outputs, limits: jsonObject(x.limits), source: { kind: source.kind } };
}
function sameScalar(a: Scalar, b: Scalar): boolean {
  if (a !== null && b !== null && typeof a === 'object' && typeof b === 'object') return a.error === b.error;
  return Object.is(a, b);
}
function key(cell: Address): string { return JSON.stringify([cell.sheet, cell.address]); }
function control(value: unknown, minimum = 0): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) throw new ClientError('invalid_input', 'Control fields require safe nonnegative integers');
  return value;
}
function stringifyJson(value: Json): string {
  if (typeof value === 'number') return Object.is(value, -0) ? '-0.0' : JSON.stringify(value);
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(stringifyJson).join(',') + ']';
  return '{' + Object.entries(value).map(([k, v]) => JSON.stringify(k) + ':' + stringifyJson(v)).join(',') + '}';
}

/** Strict JSON encoding retains numeric negative zero; JSON.stringify alone does not. */
export function encodeRequest(request: Request): string {
  let encoded: string;
  try { encoded = stringifyJson(json(request)); }
  catch (error) { throw new ClientError('invalid_input', error instanceof Error ? error.message : 'Invalid JSON request'); }
  if (new TextEncoder().encode(encoded).byteLength > LIMITS.max_request_bytes) throw new ClientError('resource_limit', 'Request exceeds existing protocol limit');
  return encoded;
}

export class WorkbookClient {
  private counter = 0;
  private tail: Promise<unknown> = Promise.resolve();
  private broken = false;
  private lastRevision: number | null = null;
  private observations = new Map<string, ObservedCell>();
  private selection = '';
  constructor(private readonly transport: Transport, private readonly idPrefix = 'client') {
    if (!idPrefix || new TextEncoder().encode(idPrefix).byteLength > 96) throw new ClientError('invalid_input', 'Request ID prefix must be 1–96 UTF-8 bytes');
  }
  get revision(): number | null { return this.lastRevision; }
  get capabilities() { return CLIENT_CAPABILITIES; }
  private queue<T>(run: () => Promise<T>): Promise<T> {
    const next = this.tail.then(() => { if (this.broken) throw new ClientError('closed', 'Client is closed or transport outcome is uncertain'); return run(); });
    this.tail = next.catch(error => { if (error instanceof ClientError && error.code === "protocol_error") this.broken = true; }); return next;
  }
  private mutationReceipt<T>(parse: () => T): T {
    try { return parse(); }
    catch (error) {
      if (error instanceof ClientError && error.code === 'protocol_error') throw new ClientError(error.code, error.message, true);
      throw error;
    }
  }
  private async invoke(operation: Operation, args: { [key: string]: Json }): Promise<unknown> {
    if (this.counter >= Number.MAX_SAFE_INTEGER) throw new ClientError("resource_limit", "Request ID counter exhausted");
    const request: Request = { id: this.idPrefix + '-' + (++this.counter), operation, arguments: args };
    encodeRequest(request); // Refuse nonfinite/undefined/oversized input before transport.
    let raw: unknown;
    try { raw = await this.transport.request(request); }
    catch (error) { this.broken = true; throw new ClientError('transport_error', error instanceof Error ? error.message : 'Transport failed', ['edit', 'set_inputs', 'export'].includes(operation)); }
    try {
      if (new TextEncoder().encode(stringifyJson(json(raw))).byteLength > LIMITS.max_response_bytes) fail("Transport response exceeds protocol budget");
      const response = object(raw);
      if (response.schema_version !== SCHEMA_VERSION || response.id !== request.id || response.operation !== operation || Object.keys(response).some(k => !['schema_version', 'id', 'operation', 'ok', 'revision', 'result', 'error'].includes(k))) fail('Response identity/version mismatch');
      this.lastRevision = integer(response.revision);
      if (response.ok === false) {
        if ('result' in response) fail('Failed response contains a result');
        const error = object(response.error); throw new ClientError(text(error.code), text(error.message));
      }
      if (response.ok !== true || 'error' in response || !('result' in response)) fail('Invalid response envelope');
      return response.result;
    } catch (error) {
      if (!(error instanceof ClientError) || error.code === 'protocol_error') this.broken = true;
      if (error instanceof ClientError && error.code === 'protocol_error') throw new ClientError(error.code, error.message, ['edit', 'set_inputs', 'export'].includes(operation));
      throw error;
    }
  }
  discover(): Promise<Json> { return this.queue(async () => { const result = json(await this.invoke('discover', {})); const catalog = object(result); if (catalog.schema_version !== SCHEMA_VERSION || catalog.profile !== PROFILE) fail('Unsupported operation catalog'); return result; }); }
  describe(): Promise<Description> { return this.queue(async () => description(await this.invoke('describe', {}))); }
  read(options: ReadOptions): Promise<Page> { return this.queue(async () => {
    const arguments_ = inputObject(options);
    if (options.expected_revision !== undefined) control(options.expected_revision);
    if (options.offset !== undefined) control(options.offset);
    if (options.limit !== undefined && control(options.limit, 1) > LIMITS.max_page_cells) throw new ClientError('invalid_input', 'Page limit exceeds protocol bound');
    const result = page(await this.invoke('read', arguments_));
    if (options.expected_revision !== undefined && options.expected_revision !== this.lastRevision) throw new ClientError('revision_conflict', 'Read snapshot revision changed');
    return result;
  }); }
  edit(edits: Edit[], expectedRevision: number): Promise<Mutation> {
    return this.queue(async () => {
      control(expectedRevision);
      if (expectedRevision >= Number.MAX_SAFE_INTEGER) throw new ClientError("invalid_input", "Next revision would exceed client safe integer range");
      const raw = await this.invoke('edit', { edits: inputJson(edits), expected_revision: expectedRevision });
      return this.mutationReceipt(() => {
      const x = object(raw);
      const result = { previous_revision: integer(x.previous_revision), revision: integer(x.revision), changed_cells: array(x.changed_cells).map(item => { const c = object(item); return { ...address(c), fields: array(c.fields).map(text) }; }) };
      if (result.previous_revision !== expectedRevision || result.revision !== this.lastRevision || result.revision !== expectedRevision + 1) throw new ClientError('protocol_error', 'Mutation revision receipt mismatch', true);
      return result;
      });
    });
  }
  calculate(outputs?: string[]): Promise<Calculation> { return this.queue(async () => {
    const result = calculation(await this.invoke('calculate', outputs === undefined ? {} : { outputs }));
    if (result.revision !== this.lastRevision) fail('Calculation revision receipt mismatch');
    return result;
  }); }
  export(filename: string, expectedRevision: number): Promise<ExportReceipt> {
    return this.queue(async () => {
      control(expectedRevision);
      const raw = await this.invoke('export', { filename, expected_revision: expectedRevision });
      return this.mutationReceipt(() => {
      const x = object(raw);
      const result = { filename: text(x.filename), revision: integer(x.revision), bytes: integer(x.bytes, 1) };
      if (result.filename !== filename || result.revision !== expectedRevision || result.revision !== this.lastRevision) throw new ClientError('protocol_error', 'Export receipt mismatch', true);
      return result;
      });
    });
  }
  /** Backend calculations only. Atomic publication of a bounded observed projection. */
  recalculate(expectedRevision: number, outputs?: string[], maxObservedCells = 1000): Promise<Recalculation> {
    return this.queue(async () => {
      control(expectedRevision); control(maxObservedCells, 1);
      if (maxObservedCells > 10000) throw new ClientError('resource_limit', 'Client observation cap is 10000 cells');
      const selected = outputs === undefined ? Object.keys(description(await this.invoke('describe', {})).outputs).sort() : [...outputs].sort();
      if (!selected.length || selected.length > LIMITS.max_outputs || new Set(selected).size !== selected.length) throw new ClientError('invalid_input', 'Choose unique named outputs within protocol bounds');
      const report = calculation(await this.invoke('calculate', { outputs: selected }));
      if (report.revision !== expectedRevision || this.lastRevision !== expectedRevision || report.stale) throw new ClientError('revision_conflict', 'Calculation revision changed');
      const next = new Map<string, ObservedCell>();
      let pages = 0, observationBytes = 0;
      for (const output of selected) {
        let offset = 0;
        do {
          if (++pages > 200) throw new ClientError('resource_limit', 'Observation page budget exceeded');
          const raw = object(await this.invoke('explain', { output, offset, limit: LIMITS.max_page_cells, expected_revision: expectedRevision }));
          const projection = page(raw);
          if (this.lastRevision !== expectedRevision || projection.offset !== offset || raw.output !== output) fail('Explanation revision/selection mismatch');
          for (const item of array(raw.cells)) {
            const entry = observed(item), identity = key(entry), previous = next.get(identity);
            if (previous && (!sameScalar(previous.calculated_value, entry.calculated_value) || previous.value_origin !== entry.value_origin)) fail('Inconsistent overlapping explanations');
            if (!previous) observationBytes += new TextEncoder().encode(JSON.stringify(entry)).byteLength;
            if (observationBytes > LIMITS.max_request_bytes) throw new ClientError("resource_limit", "Observed projection exceeds 1 MiB");
            next.set(identity, entry);
            if (next.size > maxObservedCells) throw new ClientError('resource_limit', 'Observed dependency closure exceeds client cap');
          }
          if (projection.next_offset === null) break;
          if (projection.next_offset <= offset) fail('Explanation pagination does not advance');
          offset = projection.next_offset;
        } while (true);
      }
      const selection = JSON.stringify(selected), before = selection === this.selection ? this.observations : new Map<string, ObservedCell>();
      const changes: CellChange[] = [];
      for (const [identity, after] of next) {
        const previous = before.get(identity);
        if (!previous || previous.value_origin !== after.value_origin || !sameScalar(previous.calculated_value, after.calculated_value)) changes.push({ sheet: after.sheet, address: after.address, before: previous ?? null, after });
      }
      // Detached copies prevent a UI from changing the next comparison baseline.
      this.observations = structuredClone(next); this.selection = selection;
      return { revision: expectedRevision, calculation: report, observed_cells: [...next.values()], changed_cells: changes, scope: 'selected-output-closures' };
    });
  }
  async close(): Promise<void> { await this.tail; this.broken = true; await this.transport.close(); }
}
