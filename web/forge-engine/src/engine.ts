// The command handler, independent of transport. A Web Worker wraps it in
// worker.ts; a WebView shell or a test can call it in-process. It owns at most
// one BrowserWorkbook (one stateless session); nothing is persisted and no
// network call is made.
//
// The session's event log (schema v1) lives in Rust next to the revision
// counter (rust/src/event_log.rs), so log order = revision order = patch
// order. This class only reads it out: `log`, `subscribe {events:true}` and
// `export {includeLog:true}`.

import type {
  CalculatedValue,
  Calculation,
  CommandMeta,
  Edit,
  EventMessage,
  LogEvent,
  LogPage,
  ReplayResult,
  Scalar,
  CommandEnvelope,
  CommandName,
  CommandResponse,
  Commands,
  EngineError,
  PatchEvent,
  RequestMessage,
  ResponseMessage,
  Result,
  Snapshot,
  SnapshotSheet,
} from './protocol';

/** The subset of the wasm-bindgen module (rust/src/wasm.rs) the engine uses. */
export interface ForgeWasm {
  openXlsx(bytes: Uint8Array): ForgeWorkbook;
  createBlankWorkbook(sheetName: string): ForgeWorkbook;
  sha256Hex?(bytes: Uint8Array): string;
  replayJson?(bytes: Uint8Array, events: string): string;
}

export interface ForgeWorkbook {
  snapshotJson(): string;
  /** Imported sheets' inherited Text layouts (absent on older builds and mocks). */
  textLayoutJson?(): string;
  revisionString(): string;
  applyEditsResultJson(editsJson: string, expectedRevision: string): string;
  submitJson(requestJson: string): string;
  startLog(name: string, engineJson: string): void;
  logJson(fromSeq: number, limit: number): string;
  exportLogNdjson(xlsxSha256: string): string;
  calculateJson(): string;
  exportXlsx(): Uint8Array;
  free(): void;
}

export interface EngineOptions {
  /** sha256 of the loaded .wasm, recorded in session.opened. */
  wasmSha256?: string | null;
}

const fail = (code: string, message: string, extra: Partial<EngineError> = {}): Result<never> => ({
  ok: false,
  error: { code, message, ...extra },
});

const messageOf = (error: unknown): string => (error instanceof Error ? error.message : String(error));

/** Forge model JSON cell -> protocol cell. */
function toCell(raw: { value?: unknown; formula?: string | null; style?: { number_format?: string } | null }) {
  const cell: { value: Scalar; formula?: string; text?: true } = { value: (raw.value ?? null) as Scalar };
  if (typeof raw.formula === 'string') cell.formula = raw.formula.replace(/^=/, '');
  if (raw.style?.number_format === '@') cell.text = true;
  return cell;
}

/** Protocol edits -> the Rust edit shape (formulas carry a leading "="). */
function toRustEdits(edits: Edit[]) {
  return edits.map((edit) => {
    const out: Record<string, unknown> = { sheet: edit.sheet, address: edit.address };
    if (edit.formula !== undefined) out.formula = edit.formula.startsWith('=') ? edit.formula : `=${edit.formula}`;
    else out.value = edit.value ?? null;
    return out;
  });
}

/**
 * JSON.stringify writes NaN and the infinities as null, and Rust treats a null
 * edit value as an explicit blank clear. Those numbers are refused first.
 * Negative zero is finite; JSON.stringify would write it as 0, but serde_json
 * (float_roundtrip) keeps the sign of -0.0, so -0 is written as -0.0, matching
 * typescript/src/client.ts stringifyJson. Every other value stays on JSON.stringify.
 */
function encodeWireJson(value: unknown): Result<string> {
  const issue = numberWireIssue(value);
  if (issue === 'nonfinite') return fail('invalid_request', 'numbers must be finite');
  return { ok: true, value: issue === 'negzero' ? stringifyJson(value) : JSON.stringify(value) };
}

function numberWireIssue(value: unknown): 'nonfinite' | 'negzero' | null {
  let negzero = false;
  const visit = (item: unknown): boolean => {
    if (typeof item === 'number') {
      if (!Number.isFinite(item)) return true;
      if (Object.is(item, -0)) negzero = true;
      return false;
    }
    if (Array.isArray(item)) {
      for (const entry of item) if (visit(entry)) return true;
      return false;
    }
    if (item !== null && typeof item === 'object') {
      for (const entry of Object.values(item)) if (visit(entry)) return true;
    }
    return false;
  };
  if (visit(value)) return 'nonfinite';
  return negzero ? 'negzero' : null;
}

/** Same shape as JSON.stringify, except -0 is the token -0.0. */
function stringifyJson(value: unknown): string {
  if (typeof value === 'number') return Object.is(value, -0) ? '-0.0' : JSON.stringify(value);
  if (value === null || typeof value !== 'object') {
    const text = JSON.stringify(value);
    return text === undefined ? 'null' : text;
  }
  if (Array.isArray(value)) {
    const parts: string[] = [];
    for (let i = 0; i < value.length; i += 1) {
      parts.push(i in value && value[i] !== undefined ? stringifyJson(value[i]) : 'null');
    }
    return `[${parts.join(',')}]`;
  }
  const parts: string[] = [];
  for (const key of Object.keys(value)) {
    const item = (value as Record<string, unknown>)[key];
    if (item === undefined) continue;
    parts.push(`${JSON.stringify(key)}:${stringifyJson(item)}`);
  }
  return `{${parts.join(',')}}`;
}

/** Forge model JSON -> protocol snapshot sheets. */
type RawLayout = { text_columns: [number, number][]; text_rows: number[]; general_rows: number[] };

function toSheets(
  raw: { sheets: { name: string; cells?: Record<string, never> }[] },
  layouts: Record<string, RawLayout> = {},
): SnapshotSheet[] {
  return raw.sheets.map((sheet) => {
    const out: SnapshotSheet = {
      name: sheet.name,
      cells: Object.fromEntries(Object.entries(sheet.cells ?? {}).map(([a, c]) => [a, toCell(c)])),
    };
    const layout = layouts[sheet.name];
    if (layout) out.textFormat = { columns: layout.text_columns, rows: layout.text_rows, generalRows: layout.general_rows };
    return out;
  });
}

export class ForgeEngine {
  private workbook: ForgeWorkbook | null = null;
  private workbookName = 'workbook';
  private patches: PatchEvent[] = [];
  private events: EventMessage[] = [];
  private eventsSubscribed = false;
  /** Next log seq not yet streamed to subscribers. */
  private streamedSeq = 0;
  private readonly wasm: ForgeWasm;
  private readonly options: EngineOptions;

  constructor(wasm: ForgeWasm, options: EngineOptions = {}) {
    this.wasm = wasm;
    this.options = options;
  }

  handle<K extends CommandName>(request: RequestMessage<K>): ResponseMessage<K> {
    let result: Result<unknown>;
    try {
      result = this.dispatch(request.command, request.args);
    } catch (error) {
      result = fail('engine_error', messageOf(error));
    }
    if (this.eventsSubscribed) this.collectEvents();
    return { id: request.id, command: request.command, result } as ResponseMessage<K>;
  }

  /**
   * Log events appended since the last drain, for clients that subscribed with
   * events:true. The transport posts them after the response and patches.
   */
  drainEvents(): EventMessage[] {
    const out = this.events;
    this.events = [];
    return out;
  }

  private collectEvents() {
    const wb = this.workbook;
    if (!wb) return;
    for (;;) {
      const page = JSON.parse(wb.logJson(this.streamedSeq, 1000)) as { events: LogEvent[]; next_seq: number };
      for (const event of page.events) this.events.push({ type: 'event', event });
      if (page.next_seq === this.streamedSeq) return;
      this.streamedSeq = page.next_seq;
    }
  }

  /** A new workbook starts a new log at seq 0. */
  private adopt(next: ForgeWorkbook, name: string) {
    this.workbook?.free();
    this.workbook = next;
    this.workbookName = name;
    this.streamedSeq = 0;
    next.startLog(name, JSON.stringify({ wasm_sha256: this.options.wasmSha256 ?? null }));
  }

  private dispatch(command: CommandName, args: unknown): Result<unknown> {
    switch (command) {
      case 'open': {
        const { bytes, name } = args as Commands['open']['args'];
        return this.open(bytes, name);
      }
      case 'newWorkbook':
        return this.newWorkbook((args as Commands['newWorkbook']['args']).sheetName);
      case 'snapshot':
        return this.withWorkbook((wb) => this.snapshot(wb));
      case 'applyEdits': {
        const { edits, expectedRevision } = args as Commands['applyEdits']['args'];
        return this.withWorkbook((wb) => this.applyEdits(wb, edits, expectedRevision));
      }
      case 'calculate':
        return this.withWorkbook((wb) => this.calculate(wb));
      case 'export': {
        const includeLog = Boolean((args as Commands['export']['args'] | undefined)?.includeLog);
        return this.withWorkbook((wb) => this.exportWorkbook(wb, includeLog));
      }
      case 'applyAndCalculate': {
        const { envelope, command } = args as Commands['applyAndCalculate']['args'];
        return this.withWorkbook((wb) => this.applyAndCalculate(wb, envelope, command));
      }
      case 'log': {
        const { fromSeq, limit } = args as Commands['log']['args'];
        return this.withWorkbook((wb) => this.readLog(wb, fromSeq, limit));
      }
      case 'subscribe': {
        const { events } = args as Commands['subscribe']['args'];
        if (events && !this.eventsSubscribed) this.streamedSeq = 0;
        this.eventsSubscribed = Boolean(events);
        return { ok: true, value: { events: this.eventsSubscribed } };
      }
      case 'replay': {
        const { baseline, events } = args as Commands['replay']['args'];
        return this.replay(baseline, events);
      }
      default:
        return fail('unknown_command', `unknown command ${String(command)}`);
    }
  }

  private withWorkbook(fn: (wb: ForgeWorkbook) => Result<unknown>): Result<unknown> {
    return this.workbook ? fn(this.workbook) : fail('no_workbook', 'open a workbook first');
  }

  private open(bytes: Uint8Array, name = 'workbook.xlsx'): Result<Commands['open']['result']> {
    if (!(bytes instanceof Uint8Array)) return fail('invalid_request', 'open needs Uint8Array bytes');
    let next: ForgeWorkbook;
    try {
      next = this.wasm.openXlsx(bytes);
    } catch (error) {
      return fail('open_failed', messageOf(error));
    }
    this.adopt(next, typeof name === 'string' && name ? name : 'workbook.xlsx');
    const snapshot = this.snapshot(next);
    return { ok: true, value: { revision: next.revisionString(), sheets: snapshot.value.sheets.map((s) => s.name) } };
  }

  private newWorkbook(sheetName: string): Result<Commands['newWorkbook']['result']> {
    if (typeof sheetName !== 'string' || sheetName.length === 0) {
      return fail('invalid_request', 'newWorkbook needs a non-empty worksheet name');
    }
    let next: ForgeWorkbook;
    try {
      next = this.wasm.createBlankWorkbook(sheetName);
    } catch (error) {
      return fail('create_failed', messageOf(error));
    }
    this.adopt(next, sheetName);
    const snapshot = this.snapshot(next);
    return { ok: true, value: { revision: next.revisionString(), sheets: snapshot.value.sheets.map((s) => s.name) } };
  }

  private snapshot(wb: ForgeWorkbook): { ok: true; value: Snapshot } {
    const raw = JSON.parse(wb.snapshotJson()) as { sheets: { name: string; cells: Record<string, never> }[] };
    const layouts = wb.textLayoutJson ? (JSON.parse(wb.textLayoutJson()) as Record<string, RawLayout>) : {};
    return { ok: true, value: { revision: wb.revisionString(), sheets: toSheets(raw, layouts) } };
  }

  private exportWorkbook(wb: ForgeWorkbook, includeLog: boolean): Result<Commands['export']['result']> {
    const bytes = wb.exportXlsx();
    if (!includeLog) return { ok: true, value: { bytes } };
    const sha = this.wasm.sha256Hex ? this.wasm.sha256Hex(bytes) : '';
    const log = wb.exportLogNdjson(sha);
    const base = this.workbookName.replace(/\.xlsx$/i, '') || 'workbook';
    return { ok: true, value: { bytes, log, logName: `${base}.forge-log.ndjson` } };
  }

  private readLog(wb: ForgeWorkbook, fromSeq: number, limit = 1000): Result<LogPage> {
    if (!Number.isInteger(fromSeq) || fromSeq < 0) return fail('invalid_request', 'fromSeq must be a non-negative integer');
    if (!Number.isInteger(limit) || limit < 1) return fail('invalid_request', 'limit must be a positive integer');
    const page = JSON.parse(wb.logJson(fromSeq, Math.min(limit, 100_000))) as { events: LogEvent[]; next_seq: number; total: number };
    return { ok: true, value: { events: page.events, nextSeq: page.next_seq, total: page.total } };
  }

  private replay(baseline: Uint8Array, events: string | LogEvent[]): Result<ReplayResult> {
    if (!this.wasm.replayJson) return fail('unsupported', 'this WASM build has no replayJson');
    if (!(baseline instanceof Uint8Array)) return fail('invalid_request', 'replay needs Uint8Array baseline bytes');
    let text: string;
    if (typeof events === 'string') text = events;
    else {
      const encoded = encodeWireJson(events);
      if (!encoded.ok) return encoded;
      text = encoded.value;
    }
    const out = JSON.parse(this.wasm.replayJson(baseline, text));
    if (!out.ok) return { ok: false, error: out.error };
    return {
      ok: true,
      value: {
        revision: out.revision,
        intentsApplied: out.intents_applied,
        calcsChecked: out.calcs_checked,
        calcEvents: out.calc_events,
        snapshot: { revision: out.revision, sheets: toSheets(out.snapshot) },
        values: out.values,
      },
    };
  }

  private applyEdits(wb: ForgeWorkbook, edits: Edit[], expectedRevision: string): Result<{ revision: string }> {
    if (!Array.isArray(edits)) return fail('invalid_request', 'edits must be an array');
    const encoded = encodeWireJson(toRustEdits(edits));
    if (!encoded.ok) return encoded;
    const envelope = JSON.parse(wb.applyEditsResultJson(encoded.value, String(expectedRevision)));
    return envelope.ok ? { ok: true, value: { revision: envelope.revision } } : { ok: false, error: envelope.error };
  }

  /**
   * Patches for commands applied since the last drain, oldest first. The
   * transport posts them to every connected client after the response.
   */
  drainPatches(): PatchEvent[] {
    const out = this.patches;
    this.patches = [];
    return out;
  }

  private applyAndCalculate(wb: ForgeWorkbook, envelope: CommandEnvelope, command?: CommandMeta): Result<CommandResponse> {
    const op = envelope?.op;
    if (!op) return fail('invalid_request', 'envelope.op is required');
    let wireOp: unknown = op;
    if (op.type === 'set_cells') {
      if (!Array.isArray(op.edits)) return fail('invalid_request', 'envelope.op.edits must be an array');
      wireOp = { ...op, edits: toRustEdits(op.edits) };
    }
    const wire = { ...envelope, op: wireOp };
    const request = command ? { envelope: wire, command } : { envelope: wire };
    const encoded = encodeWireJson(request);
    if (!encoded.ok) return encoded;
    const response = JSON.parse(wb.submitJson(encoded.value)) as CommandResponse;
    if (response.status !== 'applied') {
      const error = response.error ?? { code: 'engine_error', message: 'command rejected' };
      return { ok: false, error: response.event_id ? { ...error, event_id: response.event_id } : error };
    }
    if (!response.duplicate) {
      this.patches.push({
        type: 'patch',
        command_id: envelope.command_id,
        client_id: envelope.client_id,
        actor: envelope.actor,
        revision: response.revision,
        previous_revision: response.previous_revision,
        changed: response.changed,
        evaluated_cells: response.evaluated_cells,
      });
    }
    return { ok: true, value: response };
  }

  private calculate(wb: ForgeWorkbook): Result<Calculation> {
    const report = JSON.parse(wb.calculateJson()) as {
      revision: number;
      values: Record<string, CalculatedValue>;
      stale: boolean;
      diagnostics: unknown[];
    };
    const revision = wb.revisionString();
    if (String(report.revision) !== revision) {
      return fail('stale_calculation', 'calculation does not match the current revision', { current_revision: revision });
    }
    return { ok: true, value: { revision, values: report.values, stale: report.stale, diagnostics: report.diagnostics } };
  }
}
