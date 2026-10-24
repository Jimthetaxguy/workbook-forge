// The command handler, independent of transport. A Web Worker wraps it in
// worker.ts; a WebView shell or a test can call it in-process. It owns at most
// one BrowserWorkbook (one stateless session); nothing is persisted and no
// network call is made.

import type {
  CalculatedValue,
  Calculation,
  CellEdit,
  CellValue,
  CommandName,
  Commands,
  EngineError,
  RequestMessage,
  ResponseMessage,
  Result,
  Snapshot,
  SnapshotSheet,
} from './protocol';

/** The subset of the wasm-bindgen module (rust/src/wasm.rs) the engine uses. */
export interface ForgeWasm {
  openXlsx(bytes: Uint8Array): ForgeWorkbook;
}

export interface ForgeWorkbook {
  snapshotJson(): string;
  revisionString(): string;
  applyEditsResultJson(editsJson: string, expectedRevision: string): string;
  calculateJson(): string;
  exportXlsx(): Uint8Array;
  free(): void;
}

const fail = (code: string, message: string, extra: Partial<EngineError> = {}): Result<never> => ({
  ok: false,
  error: { code, message, ...extra },
});

const messageOf = (error: unknown): string => (error instanceof Error ? error.message : String(error));

/** Forge model JSON cell -> protocol cell. */
function toCell(raw: { value?: unknown; formula?: string | null }) {
  const cell: { value: CellValue; formula?: string } = { value: (raw.value ?? null) as CellValue };
  if (typeof raw.formula === 'string') cell.formula = raw.formula.replace(/^=/, '');
  return cell;
}

export class ForgeEngine {
  private workbook: ForgeWorkbook | null = null;

  constructor(private readonly wasm: ForgeWasm) {}

  handle<K extends CommandName>(request: RequestMessage<K>): ResponseMessage<K> {
    let result: Result<unknown>;
    try {
      result = this.dispatch(request.command, request.args);
    } catch (error) {
      result = fail('engine_error', messageOf(error));
    }
    return { id: request.id, command: request.command, result } as ResponseMessage<K>;
  }

  private dispatch(command: CommandName, args: unknown): Result<unknown> {
    switch (command) {
      case 'open':
        return this.open((args as Commands['open']['args']).bytes);
      case 'snapshot':
        return this.withWorkbook((wb) => this.snapshot(wb));
      case 'applyEdits': {
        const { edits, expectedRevision } = args as Commands['applyEdits']['args'];
        return this.withWorkbook((wb) => this.applyEdits(wb, edits, expectedRevision));
      }
      case 'calculate':
        return this.withWorkbook((wb) => this.calculate(wb));
      case 'export':
        return this.withWorkbook((wb) => ({ ok: true, value: { bytes: wb.exportXlsx() } }));
      default:
        return fail('unknown_command', `unknown command ${String(command)}`);
    }
  }

  private withWorkbook(fn: (wb: ForgeWorkbook) => Result<unknown>): Result<unknown> {
    return this.workbook ? fn(this.workbook) : fail('no_workbook', 'open a workbook first');
  }

  private open(bytes: Uint8Array): Result<Commands['open']['result']> {
    if (!(bytes instanceof Uint8Array)) return fail('invalid_request', 'open needs Uint8Array bytes');
    let next: ForgeWorkbook;
    try {
      next = this.wasm.openXlsx(bytes);
    } catch (error) {
      return fail('open_failed', messageOf(error));
    }
    this.workbook?.free();
    this.workbook = next;
    const snapshot = this.snapshot(next);
    return { ok: true, value: { revision: next.revisionString(), sheets: snapshot.value.sheets.map((s) => s.name) } };
  }

  private snapshot(wb: ForgeWorkbook): { ok: true; value: Snapshot } {
    const raw = JSON.parse(wb.snapshotJson()) as { sheets: { name: string; cells: Record<string, never> }[] };
    const sheets: SnapshotSheet[] = raw.sheets.map((sheet) => ({
      name: sheet.name,
      cells: Object.fromEntries(Object.entries(sheet.cells ?? {}).map(([a, c]) => [a, toCell(c)])),
    }));
    return { ok: true, value: { revision: wb.revisionString(), sheets } };
  }

  private applyEdits(wb: ForgeWorkbook, edits: CellEdit[], expectedRevision: string): Result<{ revision: string }> {
    if (!Array.isArray(edits)) return fail('invalid_request', 'edits must be an array');
    const payload = edits.map((edit) => {
      const out: Record<string, unknown> = { sheet: edit.sheet, address: edit.address };
      if (edit.formula !== undefined) out.formula = edit.formula.startsWith('=') ? edit.formula : `=${edit.formula}`;
      else out.value = edit.value ?? null;
      return out;
    });
    const envelope = JSON.parse(wb.applyEditsResultJson(JSON.stringify(payload), String(expectedRevision)));
    return envelope.ok ? { ok: true, value: { revision: envelope.revision } } : { ok: false, error: envelope.error };
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
