// Univer adapter: command interception + projection. It talks to Forge only
// through the @workbook-forge/engine command API; Univer's model is a view.
//
//   Univer UI --(commands)--> intercept --> Edit[] --> command envelope --> ForgeClient (worker)
//   applyAndCalculate() delta (changed cells) --> projection --> sheet.mutation.set-range-values {v, f}
//
// open() still pulls a full snapshot + calculation; each edit after that is one
// envelope round-trip that answers with only the cells that changed.
//
// Undo/redo never replays Univer mutations and keeps no history here: Ctrl+Z
// sends an `undo` intent to the worker, which reverts this actor's latest
// applied, not-undone intent from its event log (or refuses with
// undo_conflict when someone changed those cells since). Revisions only move
// forward.

import {
  CellValueType,
  CommandType,
  CustomCommandExecutionError,
  ICommandService,
  IUniverInstanceService,
  UniverInstanceType,
  type DocumentDataModel,
  type ICellData,
  type ICommandInfo,
  type IWorkbookData,
  type Workbook,
} from '@univerjs/core';
import type {
  CalculatedValue,
  ChangedCell,
  CommandEnvelope,
  CommandMeta,
  Edit,
  HistoryOp,
  PatchEvent,
  Scalar,
  ForgeClient,
  Revision,
  Snapshot,
} from '@workbook-forge/engine';
import { fromA1, toA1 } from './a1';
import type { UniverHost } from './univer-host';

const UNIT_ID = 'forge-workbook';

/** Commands that only move the view or selection and never touch cell data. */
const VIEW_COMMANDS = new Set([
  'sheet.command.move-selection',
  'sheet.command.move-selection-enter-tab',
  'sheet.command.select-all',
  'sheet.command.select-range',
  'sheet.command.expand-selection',
  'sheet.command.set-scroll-relative',
  'sheet.command.scroll-view',
  'sheet.command.scroll-to-cell',
  'sheet.command.scroll-view-reset',
  'sheet.command.set-zoom-ratio',
  'sheet.command.change-zoom-ratio',
  'sheet.command.set-zoom-ratio-from-toolbar',
  'sheet.command.set-worksheet-activate',
  'univer.command.copy',
]);
const GUARDED_PREFIXES = ['sheet.command.', 'univer.command.', 'formula.', 'sheets-formula.', 'formula-ui.command.'];
/** Mutations that mean Univer's formula engine was asked to, or did, calculate. */
const CALC_REQUEST = /^formula\.mutation\.(set-trigger-formula-calculation-start|set-formula-calculation-start|set-formula-string-batch-calculation|set-formula-dependency-calculation|set-cell-formula-dependency-calculation)$/;
const CALC_OUTPUT = /^formula\.mutation\.(set-formula-calculation-result|set-formula-calculation-notification|set-formula-string-batch-calculation-result|set-array-formula-data)$/;
/** Mutations that write cells or structure. Only the projection may run them. */
const DATA_MUTATION = /^sheet\.mutation\.(set-range-values|insert-|remove-|move-|reorder-range|set-worksheet-row-count|set-worksheet-column-count|add-worksheet-merge|insert-sheet|remove-sheet)/;

export interface Audit {
  /** Univer formula mutations that request a calculation. */
  calcRequests: string[];
  /** Univer formula mutations that carry calculated output. */
  calcOutputs: string[];
  /** Facade (univerAPI.getFormula()) calculation callbacks. */
  facadeEvents: string[];
  /** Cell or structure mutations that did not come from the projection. */
  foreignWrites: string[];
  /** Commands blocked by the adapter. */
  blocked: string[];
  /** Commands routed to Forge. */
  routed: string[];
  /** Recent command/operation ids (debug trace, capped). */
  trace: string[];
}

type Projected = { value: Scalar; f?: string };

function splitCellKey(key: string): { sheetName: string; address: string } {
  const separator = key.lastIndexOf('!');
  if (separator <= 0 || separator === key.length - 1) throw new Error(`invalid cell key ${key}`);
  return { sheetName: key.slice(0, separator), address: key.slice(separator + 1) };
}

function displayValue(value: Scalar): Scalar {
  return value !== null && typeof value === 'object' ? value.error : value;
}

function scalarFromCellData(cell: ICellData | null | undefined | void): Scalar {
  if (!cell) return null;
  if (cell.t === CellValueType.BOOLEAN) return Boolean(cell.v);
  return (cell.v ?? null) as Scalar;
}

function scalar(value: CalculatedValue | undefined): Scalar {
  if (value && typeof value === 'object' && 'rows' in value) return value.rows[0]?.[0] ?? null;
  return (value ?? null) as Scalar;
}

function toCellData(value: Scalar, formula?: string): ICellData {
  const cell: ICellData = {};
  if (value === null) cell.v = null;
  else if (typeof value === 'number') Object.assign(cell, { v: value, t: CellValueType.NUMBER });
  else if (typeof value === 'boolean') Object.assign(cell, { v: value ? 1 : 0, t: CellValueType.BOOLEAN });
  else if (typeof value === 'string') Object.assign(cell, { v: value, t: CellValueType.STRING });
  else Object.assign(cell, { v: value.error, t: CellValueType.STRING });
  cell.f = formula ? `=${formula}` : null;
  return cell;
}

function parseInput(cell: ICellData): { value: Scalar } | { formula: string } | null {
  if (typeof cell.f === 'string' && cell.f.startsWith('=')) return { formula: cell.f.slice(1) };
  let raw: unknown = cell.v;
  if (raw === undefined && cell.p?.body?.dataStream) raw = cell.p.body.dataStream.replace(/\r\n$/, '');
  if (raw === undefined || raw === null || raw === '') return raw === undefined ? null : { value: null };
  if (typeof raw === 'number' || typeof raw === 'boolean') return { value: raw };
  const text = String(raw);
  if (text.startsWith('=')) return { formula: text.slice(1) };
  if (/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(text.trim())) return { value: Number(text) };
  if (/^(true|false)$/i.test(text)) return { value: text.toLowerCase() === 'true' };
  return { value: text };
}

export class ForgeUniverAdapter {
  readonly audit: Audit = { calcRequests: [], calcOutputs: [], facadeEvents: [], foreignWrites: [], blocked: [], routed: [], trace: [] };
  readonly notices: string[] = [];
  revision: Revision = '0';
  /** One per tab; client_seq counts this tab's envelopes. */
  readonly clientId: string = crypto.randomUUID();
  private clientSeq = 0;
  /** performance.now() when Univer last committed a cell edit (routed to Forge). */
  lastCommitAt = 0;
  /** performance.now() when the last Forge response was written into Univer. */
  lastPaintAt = 0;
  private projected = new Map<string, Projected>();
  private sheetIds = new Map<string, string>();
  private projecting = false;
  private queue: Promise<unknown> = Promise.resolve();
  private readonly commands: ICommandService;
  private readonly instances: IUniverInstanceService;
  private onNotice: (text: string) => void = () => {};

  constructor(
    readonly host: UniverHost,
    readonly client: ForgeClient,
  ) {
    const injector = host.univer.__getInjector();
    this.commands = injector.get(ICommandService);
    this.instances = injector.get(IUniverInstanceService);
    this.commands.beforeCommandExecuted((info) => this.intercept(info));
    this.client.onPatch((patch) => this.onPatch(patch));
    this.spyOnFacade();
  }

  setNoticeSink(sink: (text: string) => void) {
    this.onNotice = sink;
  }

  /** Open .xlsx bytes in Forge and project them into a fresh Univer workbook. */
  async open(bytes: Uint8Array, name = 'Workbook'): Promise<void> {
    const opened = await this.client.open(bytes, name);
    if (!opened.ok) throw new Error(`${opened.error.code}: ${opened.error.message}`);
    await this.projectCurrentWorkbook(name);
  }

  /** Start an authored workbook whose cells can be added anywhere in the grid. */
  async newWorkbook(name = 'Sheet1'): Promise<void> {
    const created = await this.client.newWorkbook(name);
    if (!created.ok) throw new Error(`${created.error.code}: ${created.error.message}`);
    await this.projectCurrentWorkbook(name);
  }

  private async projectCurrentWorkbook(name: string): Promise<void> {
    const snapshot = await this.client.snapshot();
    if (!snapshot.ok) throw new Error(`${snapshot.error.code}: ${snapshot.error.message}`);
    this.revision = snapshot.value.revision;
    this.projected.clear();
    this.sheetIds.clear();
    const calculated = await this.client.calculate();
    if (!calculated.ok) throw new Error(`${calculated.error.code}: ${calculated.error.message}`);
    if (calculated.value.revision !== snapshot.value.revision) throw new Error('revision drift while opening workbook');
    const calc = calculated.value.values;
    const data: IWorkbookData = {
      id: UNIT_ID,
      name,
      appVersion: '1.2.0',
      locale: 'enUS' as IWorkbookData['locale'],
      styles: {},
      sheetOrder: [],
      sheets: {},
    };
    snapshot.value.sheets.forEach((sheet, index) => {
      const id = `sheet-${index}`;
      this.sheetIds.set(sheet.name, id);
      data.sheetOrder.push(id);
      let rows = 20;
      let cols = 10;
      const cellData: Record<number, Record<number, ICellData>> = {};
      for (const [address, cell] of Object.entries(sheet.cells)) {
        const { row, col } = fromA1(address);
        rows = Math.max(rows, row + 5);
        cols = Math.max(cols, col + 5);
        const value = cell.formula !== undefined ? scalar(calc[`${sheet.name}!${address}`]) : cell.value;
        (cellData[row] ??= {})[col] = toCellData(value, cell.formula);
        this.projected.set(`${sheet.name}!${address}`, { value, f: cell.formula });
      }
      data.sheets[id] = { id, name: sheet.name, rowCount: rows, columnCount: cols, cellData };
    });
    this.projecting = true;
    try {
      if (this.workbook()) this.host.univerAPI.disposeUnit(UNIT_ID);
      this.host.univerAPI.createWorkbook(data);
    } finally {
      this.projecting = false;
    }
  }

  /** Export the Forge workbook. Univer's model is never serialized. */
  async exportXlsx(): Promise<Uint8Array> {
    await this.queue;
    const result = await this.client.export();
    if (!result.ok) throw new Error(`${result.error.code}: ${result.error.message}`);
    return result.value.bytes;
  }

  /** Wait until every routed edit has been applied and projected. */
  idle(): Promise<unknown> {
    return this.queue;
  }

  /** Undo this tab's latest applied, not-undone intent (worker-side history). */
  undo() {
    return this.enqueue(() => this.history('undo'));
  }

  /** Redo this tab's latest undo (worker-side history). */
  redo() {
    return this.enqueue(() => this.history('redo'));
  }

  /** Route user edits (sheet name + A1) through Forge. */
  edit(edits: Edit[], label = 'edit', command: CommandMeta = { source: 'api' }) {
    return this.enqueue(() => this.applyToForge({ type: 'set_cells', edits }, label, command));
  }

  /** Cells whose Univer value or formula differs from the last projection. */
  divergence(): string[] {
    return this.divergenceEntries().map(({ message }) => message);
  }

  private divergenceEntries(): { key: string; message: string }[] {
    const out: { key: string; message: string }[] = [];
    const workbook = this.workbook();
    if (!workbook) return [{ key: '', message: 'no workbook' }];
    for (const [key, expected] of this.projected) {
      const { sheetName, address } = splitCellKey(key);
      const sheet = workbook.getSheetBySheetId(this.sheetIds.get(sheetName) ?? '');
      const { row, col } = fromA1(address);
      const raw = sheet?.getCellRaw(row, col);
      const shown = sheet?.getCell(row, col);
      const f = raw?.f ? String(raw.f).replace(/^=/, '') : undefined;
      if (f !== expected.f) out.push({ key, message: `${key} formula ${f} != ${expected.f}` });
      for (const [label, cell] of [['raw', raw], ['displayed', shown]] as const) {
        const value = scalarFromCellData(cell);
        if (value !== displayValue(expected.value)) {
          out.push({ key, message: `${key} ${label} ${String(value)} != ${String(displayValue(expected.value))}` });
        }
      }
    }
    return out;
  }

  /** What Univer shows for one cell: raw model value, displayed value, formula. */
  cell(sheetName: string, address: string) {
    const sheet = this.workbook()?.getSheetBySheetId(this.sheetIds.get(sheetName) ?? '');
    const { row, col } = fromA1(address);
    const raw = sheet?.getCellRaw(row, col);
    const shown = sheet?.getCell(row, col);
    return { raw: scalarFromCellData(raw), displayed: scalarFromCellData(shown), formula: raw?.f ?? null };
  }

  /** Ids of every doc unit (editors) Univer has created. */
  docUnitIds(): string[] {
    return this.instances.getAllUnitsForType<DocumentDataModel>(UniverInstanceType.UNIVER_DOC).map((u) => u.getUnitId());
  }

  /** Text currently in Univer's formula bar editor. */
  formulaBarText(): string | null {
    const doc = this.instances.getUnit<DocumentDataModel>(
      '__INTERNAL_EDITOR__DOCS_FORMULA_BAR',
      UniverInstanceType.UNIVER_DOC,
    );
    const text = doc?.getBody()?.dataStream;
    return text === undefined ? null : text.replace(/\r\n$/, '');
  }

  // ---- internals -------------------------------------------------------

  private workbook(): Workbook | null {
    return this.instances.getUnit<Workbook>(UNIT_ID, UniverInstanceType.UNIVER_SHEET) ?? null;
  }

  private enqueue(task: () => Promise<unknown>) {
    const next = this.queue.then(task).catch((error) => this.notice(`Adapter error: ${String(error)}`));
    this.queue = next;
    return next;
  }

  private notice(text: string) {
    this.notices.push(text);
    this.onNotice(text);
  }

  private async history(type: HistoryOp['type']) {
    await this.applyToForge({ type, target: null }, type, { source: 'history', univer: `univer.command.${type}` });
  }

  private envelope(op: CommandEnvelope['op']): CommandEnvelope {
    this.clientSeq += 1;
    return {
      v: 1,
      command_id: crypto.randomUUID(),
      client_id: this.clientId,
      client_seq: String(this.clientSeq),
      idempotency_key: null,
      actor: { kind: 'human', id: this.clientId, on_behalf_of: null },
      base_revision: this.revision,
      mode: 'strict',
      preconditions: [],
      op,
    };
  }

  private async applyToForge(op: CommandEnvelope['op'], label: string, command: CommandMeta): Promise<boolean> {
    const result = await this.client.applyAndCalculate(this.envelope(op), command);
    if (!result.ok) {
      const e = result.error;
      if (e.code === 'nothing_to_undo') return (this.notice('Nothing to undo.'), false);
      if (e.code === 'nothing_to_redo') return (this.notice('Nothing to redo.'), false);
      const revisions = e.current_revision ? ` (expected r${e.expected_revision}, Forge at r${e.current_revision})` : '';
      this.notice(`Forge rejected ${label}: ${e.code}: ${e.message}${revisions}`);
      this.reprojectAll();
      return false;
    }
    this.revision = result.value.revision;
    // Repaint the delta, plus the edited cells so any editor residue in Univer
    // is overwritten even when Forge reports no change for them.
    const edited = op.type === 'set_cells' ? op.edits.map((edit) => `${edit.sheet}!${edit.address}`) : [];
    this.applyChanged(result.value.changed, edited);
    this.lastPaintAt = performance.now();
    return true;
  }

  /** Patches from other clients (agents, other tabs). Our own arrive as responses. */
  private onPatch(patch: PatchEvent) {
    if (patch.client_id === this.clientId) return;
    void this.enqueue(async () => {
      if (patch.previous_revision === this.revision) {
        this.revision = patch.revision;
        this.applyChanged(patch.changed, []);
      } else if (BigInt(patch.revision) > BigInt(this.revision)) {
        this.revision = patch.revision;
        await this.refresh();
      }
    });
  }

  /** Write changed cells into the projection, the inverse cache and Univer. */
  private applyChanged(changed: ChangedCell[], alsoRepaint: string[]) {
    const bySheet = new Map<string, Record<number, Record<number, ICellData>>>();
    const repaint = new Set(alsoRepaint);
    const put = (sheet: string, address: string, data: ICellData) => {
      const { row, col } = fromA1(address);
      const matrix = bySheet.get(sheet) ?? {};
      (matrix[row] ??= {})[col] = data;
      bySheet.set(sheet, matrix);
    };
    for (const cell of changed) {
      const key = `${cell.sheet}!${cell.address}`;
      const formula = cell.formula ?? undefined;
      const value = scalar(cell.value);
      const data = toCellData(value, formula);
      this.projected.set(key, { value, f: formula });
      repaint.delete(key);
      put(cell.sheet, cell.address, data);
    }
    for (const key of repaint) {
      const prior = this.projected.get(key);
      if (!prior) continue;
      const { sheetName, address } = splitCellKey(key);
      put(sheetName, address, toCellData(prior.value, prior.f));
    }
    this.write(bySheet);
  }

  private async read(): Promise<[Snapshot, Record<string, CalculatedValue>]> {
    const [snapshot, calc] = await Promise.all([this.client.snapshot(), this.client.calculate()]);
    if (!snapshot.ok) throw new Error(snapshot.error.code);
    if (!calc.ok) throw new Error(calc.error.code);
    if (snapshot.value.revision !== this.revision || calc.value.revision !== this.revision) {
      throw new Error(`revision drift: adapter r${this.revision}, snapshot r${snapshot.value.revision}, calc r${calc.value.revision}`);
    }
    return [snapshot.value, calc.value.values];
  }

  /** Pull Forge's state and write every changed cell into Univer. */
  private async refresh() {
    const [snapshot, calc] = await this.read();
    const bySheet = new Map<string, Record<number, Record<number, ICellData>>>();
    const diverged = new Set(this.divergenceEntries().map(({ key }) => key));
    for (const sheet of snapshot.sheets) {
      for (const [address, cell] of Object.entries(sheet.cells)) {
        const key = `${sheet.name}!${address}`;
        const value = cell.formula !== undefined ? scalar(calc[key]) : cell.value;
        const data = toCellData(value, cell.formula);
        const next: Projected = { value, f: cell.formula };
        const prev = this.projected.get(key);
        this.projected.set(key, next);
        if (
          prev &&
          displayValue(prev.value) === displayValue(next.value) &&
          prev.f === next.f &&
          !diverged.has(key)
        ) continue;
        const { row, col } = fromA1(address);
        const matrix = bySheet.get(sheet.name) ?? {};
        (matrix[row] ??= {})[col] = data;
        bySheet.set(sheet.name, matrix);
      }
    }
    this.write(bySheet);
  }

  /** Restore Univer to the last projection, e.g. after a rejected edit. */
  private reprojectAll() {
    const bySheet = new Map<string, Record<number, Record<number, ICellData>>>();
    for (const [key, cell] of this.projected) {
      const { sheetName, address } = splitCellKey(key);
      const { row, col } = fromA1(address);
      const matrix = bySheet.get(sheetName) ?? {};
      (matrix[row] ??= {})[col] = toCellData(cell.value, cell.f);
      bySheet.set(sheetName, matrix);
    }
    this.write(bySheet);
  }

  private write(bySheet: Map<string, Record<number, Record<number, ICellData>>>) {
    this.projecting = true;
    try {
      for (const [sheet, cellValue] of bySheet) {
        this.commands.syncExecuteCommand(
          'sheet.mutation.set-range-values',
          { unitId: UNIT_ID, subUnitId: this.sheetIds.get(sheet), cellValue },
          { onlyLocal: true },
        );
      }
    } finally {
      this.projecting = false;
    }
  }

  private sheetName(subUnitId: string | undefined): string | undefined {
    if (!subUnitId) return this.workbook()?.getActiveSheet()?.getName();
    for (const [name, id] of this.sheetIds) if (id === subUnitId) return name;
    return undefined;
  }

  private intercept(info: Readonly<ICommandInfo>) {
    const id = info.id;
    if (this.audit.trace.push(`${info.type}:${id}`) > 300) this.audit.trace.shift();
    if (info.type === CommandType.MUTATION) {
      if (CALC_REQUEST.test(id)) this.audit.calcRequests.push(id);
      if (CALC_OUTPUT.test(id)) this.audit.calcOutputs.push(id);
      if (DATA_MUTATION.test(id) && !this.projecting) this.audit.foreignWrites.push(id);
      return;
    }
    if (info.type !== CommandType.COMMAND || this.projecting) return;
    if (id === 'univer.command.undo' || id === 'univer.command.redo') {
      const focused = this.instances.getFocusedUnit();
      if (focused && focused.type === UniverInstanceType.UNIVER_DOC) return; // in-cell editor text undo
      this.audit.routed.push(id);
      void (id === 'univer.command.undo' ? this.undo() : this.redo());
      throw new CustomCommandExecutionError(`${id} routed to Forge history`);
    }
    if (id === 'sheet.command.set-range-values') {
      const edits = this.translateSetRangeValues(info.params as Record<string, unknown>);
      if (typeof edits === 'string') {
        this.block(id, edits);
      }
      this.audit.routed.push(id);
      this.lastCommitAt = performance.now();
      void this.edit(edits, 'cell edit', { source: 'editor', univer: id });
      throw new CustomCommandExecutionError(`${id} routed to Forge`);
    }
    if (VIEW_COMMANDS.has(id)) return;
    if (GUARDED_PREFIXES.some((prefix) => id.startsWith(prefix))) this.block(id, 'outside the Forge spike scope (value/formula edits, undo/redo only)');
  }

  private block(id: string, reason: string): never {
    this.audit.blocked.push(id);
    this.notice(`Blocked ${id}: ${reason}.`);
    throw new CustomCommandExecutionError(`${id} blocked`);
  }

  /** SetRangeValuesCommand params -> Forge edits, or a reason to refuse. */
  private translateSetRangeValues(params: Record<string, unknown>): Edit[] | string {
    const sheet = this.sheetName(params.subUnitId as string | undefined);
    if (!sheet) return 'unknown sheet';
    const value = params.value as unknown;
    const range = params.range as { startRow: number; startColumn: number; endRow: number; endColumn: number } | undefined;
    const cells: { row: number; col: number; cell: ICellData }[] = [];
    const isCell = (v: unknown) =>
      !!v && typeof v === 'object' && ['v', 'f', 'p', 's', 'si', 't'].some((k) => k in (v as object));
    if (Array.isArray(value) && range) {
      value.forEach((row: ICellData[], r: number) => row.forEach((cell, c) => cells.push({ row: range.startRow + r, col: range.startColumn + c, cell })));
    } else if (isCell(value)) {
      const ranges = range ? [range] : (this.host.univerAPI.getActiveWorkbook()?.getActiveRange()?.getRange() ? [this.host.univerAPI.getActiveWorkbook()!.getActiveRange()!.getRange()] : []);
      for (const rg of ranges) for (let r = rg.startRow; r <= rg.endRow; r++) for (let c = rg.startColumn; c <= rg.endColumn; c++) cells.push({ row: r, col: c, cell: value as ICellData });
    } else if (value && typeof value === 'object') {
      for (const [r, cols] of Object.entries(value as Record<string, Record<string, ICellData>>)) {
        for (const [c, cell] of Object.entries(cols)) cells.push({ row: Number(r), col: Number(c), cell });
      }
    }
    if (cells.length === 0) return 'no cells';
    const edits: Edit[] = [];
    for (const { row, col, cell } of cells) {
      const input = parseInput(cell ?? {});
      if (!input) return 'style-only change is outside the spike scope';
      edits.push({ sheet, address: toA1(row, col), ...input });
    }
    return edits;
  }

  private spyOnFacade() {
    const api = this.host.univerAPI as unknown as { getFormula?: () => Record<string, (cb: (arg: unknown) => void) => unknown> };
    if (!this.host.formulaPlugins || typeof api.getFormula !== 'function') return;
    try {
      const formula = api.getFormula();
      formula.calculationStart?.((force) => this.audit.facadeEvents.push(`calculationStart(force=${String(force)})`));
      formula.calculationEnd?.(() => this.audit.facadeEvents.push('calculationEnd'));
      formula.calculationResultApplied?.(() => this.audit.facadeEvents.push('calculationResultApplied'));
    } catch (error) {
      this.audit.facadeEvents.push(`facade unavailable: ${String(error)}`);
    }
  }
}
