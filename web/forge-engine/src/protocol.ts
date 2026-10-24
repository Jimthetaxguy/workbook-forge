// Host-agnostic command API for the Forge engine. No UI or framework types:
// the Univer adapter, a Tauri/wry WebView shell, and future in-page MCP tools
// all speak this protocol. Every payload is structured-clone safe and every
// revision is a decimal string (no BigInt crosses the boundary).

/** A workbook revision as a decimal string, e.g. "0", "12". */
export type Revision = string;

// Names and shapes below match the agent-workbook-v1 TypeScript client in
// draft PR #19 (typescript/src/client.ts) where they overlap: ExcelError,
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
 * One existing-cell edit: exactly one of value or formula (formula text with or
 * without the leading "="). PR #19's Edit also allows style edits; imported
 * workbooks reject those, so this subset omits them.
 */
export type Edit = Address & ({ value: Scalar; formula?: never } | { formula: string; value?: never });

export interface EngineError {
  code: string;
  message: string;
  expected_revision?: Revision;
  current_revision?: Revision;
}

export type Result<T> = { ok: true; value: T } | { ok: false; error: EngineError };

export interface SnapshotCell {
  value: Scalar;
  /** Formula without the leading "=", when the cell holds one. */
  formula?: string;
}

export interface SnapshotSheet {
  name: string;
  cells: Record<string, SnapshotCell>;
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
}

/** Requests, keyed by command name. */
export interface Commands {
  open: { args: { bytes: Uint8Array }; result: OpenResult };
  snapshot: { args: Record<string, never>; result: Snapshot };
  applyEdits: { args: { edits: Edit[]; expectedRevision: Revision }; result: ApplyResult };
  calculate: { args: Record<string, never>; result: Calculation };
  export: { args: Record<string, never>; result: ExportResult };
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

export const COMMANDS: readonly CommandName[] = ['open', 'snapshot', 'applyEdits', 'calculate', 'export'];
