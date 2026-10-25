// The command handler, independent of transport. A Web Worker wraps it in
// worker.ts; a WebView shell or a test can call it in-process. It owns at most
// one BrowserWorkbook (one stateless session); nothing is persisted and no
// network call is made.

import type {
  CalculatedValue,
  Calculation,
  Edit,
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
}

export interface ForgeWorkbook {
  snapshotJson(): string;
  revisionString(): string;
  applyEditsResultJson(editsJson: string, expectedRevision: string): string;
  applyAndCalculateJson(envelopeJson: string): string;
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
  const cell: { value: Scalar; formula?: string } = { value: (raw.value ?? null) as Scalar };
  if (typeof raw.formula === 'string') cell.formula = raw.formula.replace(/^=/, '');
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

export class ForgeEngine {
  private workbook: ForgeWorkbook | null = null;
  private patches: PatchEvent[] = [];
  private readonly wasm: ForgeWasm;

  constructor(wasm: ForgeWasm) {
    this.wasm = wasm;
  }

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
      case 'applyAndCalculate': {
        const { envelope } = args as Commands['applyAndCalculate']['args'];
        return this.withWorkbook((wb) => this.applyAndCalculate(wb, envelope));
      }
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

  private applyEdits(wb: ForgeWorkbook, edits: Edit[], expectedRevision: string): Result<{ revision: string }> {
    if (!Array.isArray(edits)) return fail('invalid_request', 'edits must be an array');
    const payload = toRustEdits(edits);
    const envelope = JSON.parse(wb.applyEditsResultJson(JSON.stringify(payload), String(expectedRevision)));
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

  private applyAndCalculate(wb: ForgeWorkbook, envelope: CommandEnvelope): Result<CommandResponse> {
    const ops = envelope?.op;
    if (!ops || !Array.isArray(ops.edits)) return fail('invalid_request', 'envelope.op.edits must be an array');
    const wire = { ...envelope, op: { ...ops, edits: toRustEdits(ops.edits) } };
    const response = JSON.parse(wb.applyAndCalculateJson(JSON.stringify(wire))) as CommandResponse;
    if (response.status !== 'applied') {
      return { ok: false, error: response.error ?? { code: 'engine_error', message: 'command rejected' } };
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
