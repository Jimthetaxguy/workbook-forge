// Host-agnostic command API for the Forge engine. No UI or framework types:
// the Univer adapter, a Tauri/wry WebView shell, and future in-page MCP tools
// all speak this protocol. Every payload is structured-clone safe and every
// revision is a decimal string (no BigInt crosses the boundary).

/** A workbook revision as a decimal string, e.g. "0", "12". */
export type Revision = string;

// Names and shapes below align with the agent-workbook-v1 TypeScript client
// (typescript/src/client.ts) where they overlap: ExcelError,
// Scalar, Address, Edit. Divergences are listed in docs/specs/univer-spike.md.

export type ExcelError = { error: string; message?: string };

/** A Forge scalar. Blank cells are null. */
export type Scalar = null | boolean | number | string | ExcelError;

export interface Address {
  sheet: string;
  address: string;
}

/** A calculated value: a scalar, or a spilled array. */
export type CalculatedValue = Scalar | { rows: Scalar[][] };

/**
 * One value or formula edit (formula text with or without the leading "=").
 * It may update an existing cell or insert an absent cell inside the grid
 * (#41); insertion into a merged range's non-anchor cell is refused. PR #19's
 * Edit also allows style edits; imported workbooks reject those, so this
 * subset omits them.
 */
export type Edit = Address & ({ value: Scalar; formula?: never } | { formula: string; value?: never });

export interface EngineError {
  code: string;
  message: string;
  expected_revision?: Revision;
  current_revision?: Revision;
  /** The log event that recorded this rejection, when the command was logged. */
  event_id?: string;
  /** undo_conflict / redo_conflict: the event being undone or redone. */
  target?: string;
  /** undo_conflict / redo_conflict: each cell whose input changed since. */
  cells?: { sheet: string; address: string; expected: LogInput; current: LogInput; overwritten_by: string | null }[];
  /** undo_conflict / redo_conflict: the events that overwrote those cells. */
  overwritten_by?: string[];
  [detail: string]: unknown;
}

export type Result<T> = { ok: true; value: T } | { ok: false; error: EngineError };

export interface SnapshotCell {
  value: Scalar;
  /** Formula without the leading "=", when the cell holds one. */
  formula?: string;
  /**
   * True when the cell's number format is Text (`@`). Typed input stays text
   * there: `00123` keeps its leading zeros and is never read as a number.
   */
  text?: true;
}

/**
 * Where a cell inserted where none existed inherits the Text (`@`) format, as
 * Excel does: a row listed in `rows` or `generalRows` (a row with its own
 * format) decides for every new cell in it; otherwise a column inside one of
 * the 1-based `[min, max]` `columns` ranges is Text. Cells the workbook already
 * holds keep their own format (`SnapshotCell.text`).
 */
export interface TextFormatLayout {
  columns: [number, number][];
  rows: number[];
  generalRows: number[];
}

export interface SnapshotSheet {
  name: string;
  cells: Record<string, SnapshotCell>;
  /** Present when the imported sheet's row or column styles are Text (`@`). */
  textFormat?: TextFormatLayout;
}

export interface Snapshot {
  revision: Revision;
  sheets: SnapshotSheet[];
}

export interface Calculation {
  revision: Revision;
  /** Keyed "Sheet!A1". */
  values: Record<string, CalculatedValue>;
  stale: boolean;
  diagnostics: unknown[];
}

export interface OpenResult {
  revision: Revision;
  sheets: string[];
}

export interface ApplyResult {
  revision: Revision;
}

export interface ExportResult {
  bytes: Uint8Array;
  /** With includeLog: the session log as NDJSON, ending in session.exported. */
  log?: string;
  /** With includeLog: suggested file name, `<name>.forge-log.ndjson`. */
  logName?: string;
}

// ---- Command envelope (multi-user contract section 6.1) -------------------
// One envelope per intent; the worker applies it, calculates, and answers with
// a delta. Only mode "strict" (compare-and-swap on base_revision) is
// implemented; "rebase" fails closed with unsupported_mode and non-empty
// preconditions with unsupported_precondition.

export interface Actor {
  kind: 'human' | 'agent';
  id: string;
  on_behalf_of: string | null;
}

export interface SetCellsOp {
  type: 'set_cells';
  edits: Edit[];
}

/**
 * Undo/redo as intents. Without a target, undo reverts the actor's own latest
 * applied, not-undone intent and redo reverts the actor's latest undo. The
 * worker refuses with undo_conflict (redo_conflict) when a target cell's
 * input changed since that event.
 */
export interface HistoryOp {
  type: 'undo' | 'redo';
  /** An event id (`ev_0003`); undo: an edits/redo intent, redo: an undo intent. */
  target?: string | null;
}

/** Where an intent came from; logged as command.source / command.univer. */
export interface CommandMeta {
  source: 'editor' | 'formula-bar' | 'paste' | 'fill' | 'clear' | 'api' | 'agent' | 'history';
  /** The Univer command id that produced it, when there is one. */
  univer?: string;
}

export interface CommandEnvelope {
  v: 1;
  /** Globally unique, client-generated (crypto.randomUUID()). The dedupe key. */
  command_id: string;
  /** One per tab / agent connection. */
  client_id: string;
  /** Decimal-string counter, monotonic per client_id. */
  client_seq: string;
  /** Agents set their own so a retry under a new command_id is still deduped. */
  idempotency_key: string | null;
  actor: Actor;
  base_revision: Revision;
  mode: 'strict' | 'rebase';
  preconditions: unknown[];
  op: SetCellsOp | HistoryOp;
}

/** A cell whose calculated value or input changed in a command. */
export interface ChangedCell {
  sheet: string;
  address: string;
  value: CalculatedValue;
  /** Formula without the leading "=", or null for a literal. */
  formula: string | null;
}

export interface CommandResponse {
  v: 1;
  command_id: string | null;
  status: 'applied' | 'rejected';
  revision: Revision;
  previous_revision: Revision;
  changed: ChangedCell[];
  evaluated_cells: number;
  diagnostics: unknown[];
  /** True when this is the remembered response to an earlier, identical id. */
  duplicate: boolean;
  /** The intent event that recorded this command. */
  event_id?: string;
  /** The calc event of an applied command. */
  calc_event_id?: string;
  error?: EngineError;
}

// ---- Event log (schema v1, realtime design section 4.2) -----------------

/**
 * A logged cell input. `{ absent: true }` is a cell the workbook did not hold
 * (an insertion's `before`, or an undone insertion's `after`); it differs from
 * `{ value: null }`, a blank or cleared cell the workbook holds.
 */
export type LogInput = { value: Scalar } | { formula: string } | { absent: true };

export interface LogActor {
  type: 'human' | 'agent';
  id: string;
  on_behalf_of?: string;
}

interface LogEventBase {
  v: 1;
  seq: number;
  /** `ev_0000`, `ev_0001`, ... */
  id: string;
  session: string;
}

export interface SessionOpenedEvent extends LogEventBase {
  kind: 'session';
  op: 'opened';
  ts: string;
  revision: Revision;
  baseline: { kind: 'xlsx' | 'blank'; name: string; sha256: string | null; bytes: number; sheet?: string };
  engine: { forge: string; commit: string | null; wasm_sha256: string | null };
}

export interface SessionExportedEvent extends LogEventBase {
  kind: 'session';
  op: 'exported';
  ts: string;
  revision: Revision;
  xlsx_sha256: string | null;
}

export interface IntentEvent extends LogEventBase {
  kind: 'intent';
  ts: string;
  mono_ms: number;
  actor: LogActor | null;
  command: {
    name: 'edits' | 'undo' | 'redo';
    source: string;
    univer?: string;
    /** undo / redo: the edit intent acted on. */
    target?: string;
    /** redo: the undo event it reverses. */
    undo_event?: string;
    command_id?: string;
  };
  base: { mode: 'strict'; expectedRevision: Revision } | { mode: 'cells' | 'rebase'; baseRevision: Revision } | null;
  status: 'applied' | 'rejected';
  /** Applied: the new revision. Rejected: the revision it was refused at. */
  revision: Revision;
  /** Applied: before and after per cell. Rejected: what was asked (after only). */
  inputs: { sheet: string; address: string; before?: LogInput; after: LogInput }[];
  error: EngineError | null;
}

export interface CalcEvent extends LogEventBase {
  kind: 'calc';
  caused_by: string[];
  revision: Revision;
  reason: 'inputs';
  evaluated: number;
  changed: number;
  calc_ms: number;
  /** `[key, before, after]` for every formula cell whose value changed. */
  deltas: [key: string, before: CalculatedValue, after: CalculatedValue][];
  diagnostics: unknown[];
}

export type LogEvent = SessionOpenedEvent | SessionExportedEvent | IntentEvent | CalcEvent;

export interface LogPage {
  events: LogEvent[];
  /** Pass as fromSeq to continue. */
  nextSeq: number;
  total: number;
}

/** Posted to clients that subscribed with events:true, after patches. */
export interface EventMessage {
  type: 'event';
  event: LogEvent;
}

export interface ReplayResult {
  revision: Revision;
  intentsApplied: number;
  calcsChecked: number;
  /** Recomputed calc events; each equals the logged one. */
  calcEvents: { caused_by: string[]; revision: Revision; deltas: CalcEvent['deltas'] }[];
  snapshot: Snapshot;
  values: Record<string, CalculatedValue>;
}

/**
 * Posted by the worker to every connected client after each applied,
 * non-duplicate command, after the submitter's response.
 */
export interface PatchEvent {
  type: 'patch';
  command_id: string;
  client_id: string;
  actor: Actor;
  revision: Revision;
  previous_revision: Revision;
  changed: ChangedCell[];
  evaluated_cells: number;
}

/** Requests, keyed by command name. */
export interface Commands {
  /** `name` is recorded as the log baseline's name. */
  open: { args: { bytes: Uint8Array; name?: string }; result: OpenResult };
  newWorkbook: { args: { sheetName: string }; result: OpenResult };
  snapshot: { args: Record<string, never>; result: Snapshot };
  applyEdits: { args: { edits: Edit[]; expectedRevision: Revision }; result: ApplyResult };
  calculate: { args: Record<string, never>; result: Calculation };
  export: { args: { includeLog?: boolean }; result: ExportResult };
  /** Apply + calculate in one call; ok:true only for status "applied". Logged. */
  applyAndCalculate: { args: { envelope: CommandEnvelope; command?: CommandMeta }; result: CommandResponse };
  /** Read the session log from fromSeq (default limit 1000). */
  log: { args: { fromSeq: number; limit?: number }; result: LogPage };
  /** events:true streams every new log event as an EventMessage. */
  subscribe: { args: { events: boolean }; result: { events: boolean } };
  /** Replay a log onto its baseline bytes; any mismatch is an error. */
  replay: { args: { baseline: Uint8Array; events: string | LogEvent[] }; result: ReplayResult };
}

export type CommandName = keyof Commands;

export interface RequestMessage<K extends CommandName = CommandName> {
  id: number;
  command: K;
  args: Commands[K]['args'];
}

export interface ResponseMessage<K extends CommandName = CommandName> {
  id: number;
  command: K;
  result: Result<Commands[K]['result']>;
}
