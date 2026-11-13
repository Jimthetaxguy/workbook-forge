// Univer adapter: command interception + projection. It talks to Forge only
// through the @workbook-forge/engine command API; Univer's model is a view.
//
//   Univer UI --(commands)--> intercept --> Edit[] --> command envelope --> ForgeClient (worker)
//   applyAndCalculate() delta (changed cells) --> projection --> sheet.mutation.set-range-values {v, f}
//
// open() still pulls a full snapshot + calculation; each edit after that is one
// envelope round-trip that answers with only the cells that changed.
//
// Presentation (number formats, bold, colors, alignment, column widths) is
// projected once, from the snapshot, and is display only: Forge holds it and
// writes it back on export; nothing here can change it.
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
  HorizontalAlign,
  ICommandService,
  IUniverInstanceService,
  UniverInstanceType,
  type DocumentDataModel,
  type ICellData,
  type IColumnData,
  type ICommandInfo,
  type IStyleData,
  type IWorkbookData,
  type Workbook,
} from '@univerjs/core';
import type {
  CalculatedValue,
  CellStyle,
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
  TextFormatLayout,
} from '@workbook-forge/engine';
import { IRenderManagerService } from '@univerjs/engine-render';
import { IEditorBridgeService, ISheetSelectionRenderService } from '@univerjs/sheets-ui';
import { IClipboardInterfaceService } from '@univerjs/ui';
import { fromA1, toA1 } from './a1';
import {
  MAX_RANGE_CELLS,
  cellCount,
  clipboardColumns,
  columns,
  contains,
  copyIdOf,
  fillSource,
  isBlank,
  isDateFormat,
  parseClipboardText,
  pasteShape,
  planLine,
  rows,
  union,
  type Held,
  type LinePlan,
  type Rect,
} from './range-edits';
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
  // Ctrl+D and Ctrl+R only work out the ranges and then run
  // sheet.command.auto-fill, which is routed to Forge below.
  'sheet.command.copy-down',
  'sheet.command.copy-right',
]);
/** Excel's grid: 1,048,576 rows by 16,384 columns. */
const EXCEL_ROWS = 1_048_576;
const EXCEL_COLUMNS = 16_384;
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
}

type Projected = Held;

/** The part of Univer's selection control that remembers the fill handle's last drag. */
interface FillHandleState {
  _controlExtension?: { _targetSelection?: { startRow: number; endRow: number; startColumn: number; endColumn: number } };
}

/** A copy made on this page: where it came from and what Forge held there. */
interface CopiedRange {
  /** Univer's id for the copy, read back from the clipboard HTML at paste. */
  id: string;
  rect: Rect;
  /** `row,col` -> the cell, for cells that held something. Absent when too large. */
  cells: Map<string, Held> | null;
}

/** One cell to write, and the cell it copies (absent or blank: clear it). */
interface CopyStep {
  row: number;
  col: number;
  from: { row: number; col: number; cell: Held | undefined };
}

type UniverRange = { startRow: number; endRow: number; startColumn: number; endColumn: number };

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

/** A `{ rows }` value is one cell only when it has a single row and a single column. */
function isSingleCellArray(value: CalculatedValue): boolean {
  if (!value || typeof value !== 'object' || !('rows' in value) || !Array.isArray(value.rows)) return false;
  const { rows } = value;
  return rows.length === 1 && Array.isArray(rows[0]) && rows[0].length === 1;
}

/** The smallest grid a sheet is shown with, so there is room to work. */
export const MIN_ROWS = 200;
export const MIN_COLUMNS = 26;
/** Blank rows and columns kept past the last cell the workbook holds. */
const ROW_MARGIN = 100;
const COLUMN_MARGIN = 10;

/**
 * An Excel column width in character units as screen pixels. Excel measures a
 * column in widths of the default font's widest digit; for the default
 * Calibri 11 that digit is 7 pixels at 100% (ECMA-376 18.3.1.13). A workbook
 * with another default font is drawn slightly narrower or wider than Excel.
 */
export function columnWidthPixels(width: number): number {
  return Math.max(0, Math.round(width * 7));
}

/** `RRGGBB` or `AARRGGBB` workbook hex -> `#RRGGBB`. Alpha is not drawn. */
function cssColor(hex: string): string | null {
  if (!/^([0-9a-f]{6}|[0-9a-f]{8})$/i.test(hex)) return null;
  return `#${hex.slice(-6).toUpperCase()}`;
}

const ALIGN = { left: HorizontalAlign.LEFT, center: HorizontalAlign.CENTER, right: HorizontalAlign.RIGHT } as const;

/**
 * Protocol presentation -> Univer style, or undefined when nothing is drawn.
 * Text (`@`) is left to the adapter's own typed-input rule (`parseInput`) and
 * is not handed to Univer as a pattern.
 */
export function toUniverStyle(style: CellStyle | undefined): IStyleData | undefined {
  if (!style) return undefined;
  const out: IStyleData = {};
  if (style.numberFormat && style.numberFormat !== '@') out.n = { pattern: style.numberFormat };
  if (style.bold) out.bl = 1;
  const font = style.fontColor ? cssColor(style.fontColor) : null;
  if (font) out.cl = { rgb: font };
  const fill = style.fillColor ? cssColor(style.fillColor) : null;
  if (fill) out.bg = { rgb: fill };
  if (style.horizontal) out.ht = ALIGN[style.horizontal];
  return Object.keys(out).length > 0 ? out : undefined;
}

/** Numbers and counts for the cells in a selection, as a status bar shows. */
export interface SelectionSummary {
  /** Cells holding anything (a value or an error). */
  count: number;
  /** Cells holding a number. */
  numbers: number;
  sum: number;
  min: number | null;
  max: number | null;
}

/** A formula result that is more than one cell, kept for the preview panel. */
export interface ArrayResult {
  key: string;
  rows: Scalar[][];
}

export type NoticeKind = 'info' | 'error';

/** How every notice about a change Forge refused begins. */
export const REJECTED = 'Change not applied.';
/** Shown when the fill handle meets a series Excel would continue and this page does not build. */
export const SERIES_REFUSED =
  'Fill continues numbers (1, 2, 3), text ending in a number (Item 1) and day and month names. Dates, times and other patterns are not available yet. Ctrl+D and Ctrl+R copy as they are.';

/** Plain words for a refused Univer command, most specific pattern first. */
const REFUSALS: [RegExp, string][] = [
  [/\.cut$/, 'Cut is not available yet. Copy and paste, then clear the original with Delete.'],
  [/paste/, 'Paste with Ctrl+V (Cmd+V on a Mac). Pasting from the menu and Paste Special are not available yet.'],
  [/refill|fill-/, 'Fill options are not available yet.'],
  [/clear-selection-(format|all)/, 'Clearing formats is not available yet. Delete clears the contents.'],
  [/delete-range|clear-/, 'That way of clearing cells is not available yet. Select the cells and press Delete.'],
  [/insert-sheet|remove-sheet|copy-sheet|set-worksheet-(name|order|hide|show)|set-tab-color/, 'Adding, removing and renaming sheets is not available yet.'],
  [/insert-(row|col|range|multi)/, 'Inserting rows and columns is not available yet.'],
  [/remove-(row|col)|delete-/, 'Deleting rows and columns is not available yet.'],
  [/merge/, 'Merging cells is not available yet.'],
  [/move-(range|rows|cols)|reorder|sort|filter/, 'Moving and sorting cells is not available yet.'],
  [/set-(row|col|worksheet-row|worksheet-col)|delta-(row|column)|hide|freeze/, 'Resizing, hiding and freezing rows and columns is not available yet.'],
  [/style|bold|italic|underline|strike|font|color|border|align|wrap|rotation|numfmt|currency|percent|decimal|format/, 'Formatting changes are not available yet.'],
];

/** The sentence shown when a command is refused. */
export function refusalText(id: string): string {
  return REFUSALS.find(([pattern]) => pattern.test(id))?.[1] ?? 'That action is not available yet.';
}

/**
 * Project a calculated value onto one cell. A `{ rows }` array is written only
 * when it is 1×1. Any other shape is left blank: this view does not spill and
 * does not paint the top-left cell.
 */
function scalar(value: CalculatedValue | undefined): Scalar {
  if (value && typeof value === 'object' && 'rows' in value) {
    if (isSingleCellArray(value)) return value.rows[0]?.[0] ?? null;
    return null;
  }
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

/**
 * Read one typed cell. In a Text-formatted (`@`) cell, or for a force-string
 * entry (a leading apostrophe), everything typed stays text:
 * `00123` keeps its zeros and `=A1` is the text "=A1", not a formula. A
 * leading apostrophe is the entry marker and is not part of the value.
 */
export function parseInput(cell: ICellData, textFormat = false): { value: Scalar } | { formula: string } | null {
  let raw: unknown = cell.v;
  // Univer's editor leaves v null for a formula-shaped entry unless the cell
  // style pattern is @. A text cell still stores that entry as text.
  // It also leaves v null, with the text in p, when an edit starts from what
  // the cell already holds (a double click, F2, a double tap) and characters
  // are added: the new characters carry a text color the old ones do not, so
  // Univer hands the entry over as rich text. What was typed is its text.
  // Univer ends each line of that text but the last with \r; a line break
  // inside a cell is \n.
  if (cell.p?.body?.dataStream && (raw === undefined || raw === null)) {
    raw = cell.p.body.dataStream.replace(/\r\n$/, '').replace(/\r/g, '\n');
  }
  const forced = cell.t === CellValueType.FORCE_STRING || (typeof raw === 'string' && raw.startsWith("'"));
  if (textFormat || forced) {
    if ((textFormat ? raw == null : raw === undefined) && typeof cell.f === 'string') raw = cell.f;
    if (raw === undefined) return null;
    if (raw === null || raw === '') return { value: null };
    const text = typeof raw === 'boolean' ? (raw ? 'TRUE' : 'FALSE') : String(raw);
    return { value: forced && text.startsWith("'") ? text.slice(1) : text };
  }
  if (typeof cell.f === 'string' && cell.f.startsWith('=')) return { formula: cell.f.slice(1) };
  if (raw === undefined || raw === null || raw === '') return raw === undefined ? null : { value: null };
  if (typeof raw === 'number' || typeof raw === 'boolean') return { value: raw };
  const text = String(raw);
  if (text.startsWith('=')) return { formula: text.slice(1) };
  if (/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(text.trim())) return { value: Number(text) };
  if (/^(true|false)$/i.test(text)) return { value: text.toLowerCase() === 'true' };
  return { value: text };
}

export class ForgeUniverAdapter {
  readonly audit: Audit = { calcRequests: [], calcOutputs: [], facadeEvents: [], foreignWrites: [], blocked: [], routed: [] };
  readonly notices: string[] = [];
  revision: Revision = '0';
  /** Queued Forge round trips not yet finished (edits, undo, redo, patches). */
  pending = 0;
  /** One per tab; client_seq counts this tab's envelopes. */
  readonly clientId: string = crypto.randomUUID();
  private clientSeq = 0;
  /** performance.now() when Univer last committed a cell edit (routed to Forge). */
  lastCommitAt = 0;
  /** performance.now() when the last Forge response was written into Univer. */
  lastPaintAt = 0;
  private projected = new Map<string, Projected>();
  /** Univer selection services already repaired (one per loaded workbook). */
  private readonly outlined = new WeakSet<object>();
  /** `Sheet!A1` keys of cells drawn with a number format: Univer shows text there. */
  private readonly formatted = new Set<string>();
  /** `Sheet!A1` -> the rows of a formula result that is more than one cell. */
  private readonly arrays = new Map<string, Scalar[][]>();
  /** `Sheet!A1` keys of Text-formatted (`@`) cells: typed input stays text. */
  private readonly textCells = new Set<string>();
  /**
   * Sheets whose row or column styles are Text (`@`), with the cells the
   * workbook held when it opened. An inserted cell takes its row style when
   * `customFormat` is set, otherwise its column style, so its input stays
   * text. Workbook Forge follows this rule; not checked against Excel.
   */
  private readonly textLayouts = new Map<string, { layout: TextFormatLayout; opened: Set<string> }>();
  /** `Sheet!A1` keys of cells drawn with a date or time format: the fill handle would continue them. */
  private readonly dateCells = new Set<string>();
  /** The selection a copy command is about to copy, until the clipboard write names it. */
  private copying: Omit<CopiedRange, 'id'> | null = null;
  /** The latest copy this page put on the clipboard. */
  private copied: CopiedRange | null = null;
  private clipboardWatched = false;
  private sheetIds = new Map<string, string>();
  private projecting = false;
  private queue: Promise<unknown> = Promise.resolve();
  private readonly commands: ICommandService;
  private readonly instances: IUniverInstanceService;
  private onNotice: (text: string, kind: NoticeKind) => void = () => {};

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
    this.watchPointer();
  }

  /**
   * Two repairs to how Univer draws the selection, both made before Univer's
   * own canvas and keyboard listeners run (capture phase).
   *
   * The fill handle keeps the range of its last drag and sends it again when
   * the handle is only clicked, from whatever cell is then selected. Forget
   * that range each time the pointer goes down, so only a real drag or a
   * double click asks for a fill. The two fields are private to Univer 1.2.0;
   * the "click on the fill handle" acceptance test fails if they move.
   *
   * See `selectionRender` for the second.
   */
  private watchPointer() {
    window.addEventListener(
      'pointerdown',
      () => {
        const controls = (this.selectionRender()?.getSelectionControls() ?? []) as unknown as FillHandleState[];
        for (const control of controls) {
          const last = control._controlExtension?._targetSelection;
          if (last) Object.assign(last, { startRow: -1, endRow: -1, startColumn: -1, endColumn: -1 });
        }
      },
      true,
    );
    window.addEventListener('keydown', () => void this.selectionRender(), true);
  }

  /**
   * Univer's selection drawing for the workbook, repaired the first time it is
   * seen. Leaving an open cell editor by clicking a cell whose formula has
   * references makes Univer's formula editor erase the selection outline a
   * moment after the editor has closed (it treats the clicked formula as one
   * still being typed). The cell stays selected with no outline and no fill
   * handle. An erase that arrives while no editor is open and a cell is
   * selected is therefore ignored. The "leaving an editor" acceptance test
   * fails without this.
   */
  private selectionRender() {
    const injector = this.host.univer.__getInjector();
    const service = injector.get(IRenderManagerService).getRenderUnitById(UNIT_ID)?.with(ISheetSelectionRenderService);
    if (service && !this.outlined.has(service)) {
      this.outlined.add(service);
      const editor = injector.get(IEditorBridgeService);
      const reset = service.resetSelectionsByModelData.bind(service);
      service.resetSelectionsByModelData = (selections) => {
        if (selections.length === 0 && !editor.isVisible().visible && this.selection().length > 0) return;
        reset(selections);
      };
    }
    return service;
  }

  setNoticeSink(sink: (text: string, kind: NoticeKind) => void) {
    this.onNotice = sink;
  }

  /** Formula results that are more than one cell, by `Sheet!A1`. */
  arrayResults(): ArrayResult[] {
    return [...this.arrays].map(([key, rows]) => ({ key, rows }));
  }

  /** The array result of one cell, when its formula returns more than one cell. */
  arrayResult(sheetName: string, address: string): Scalar[][] | null {
    return this.arrays.get(`${sheetName}!${address}`) ?? null;
  }

  /** The active sheet's name, as Forge knows it. */
  activeSheetName(): string | undefined {
    return this.sheetName(undefined);
  }

  /**
   * Count and total the cells Forge holds inside zero-based, inclusive ranges
   * of one sheet. The values are Forge's; only the adding up happens here,
   * left to right in row order, which is not a Forge calculation.
   */
  summarize(sheetName: string, ranges: { startRow: number; endRow: number; startColumn: number; endColumn: number }[]): SelectionSummary {
    const summary: SelectionSummary = { count: 0, numbers: 0, sum: 0, min: null, max: null };
    const prefix = `${sheetName}!`;
    const seen: { row: number; col: number; value: Scalar }[] = [];
    for (const [key, cell] of this.projected) {
      if (!key.startsWith(prefix) || key.lastIndexOf('!') !== prefix.length - 1) continue;
      const { row, col } = fromA1(key.slice(prefix.length));
      if (!ranges.some((r) => row >= r.startRow && row <= r.endRow && col >= r.startColumn && col <= r.endColumn)) continue;
      seen.push({ row, col, value: cell.value });
    }
    seen.sort((a, b) => a.row - b.row || a.col - b.col);
    for (const { value } of seen) {
      if (value === null || value === '') continue;
      summary.count += 1;
      if (typeof value !== 'number') continue;
      summary.numbers += 1;
      summary.sum += value;
      summary.min = summary.min === null ? value : Math.min(summary.min, value);
      summary.max = summary.max === null ? value : Math.max(summary.max, value);
    }
    return summary;
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
    this.formatted.clear();
    this.arrays.clear();
    this.sheetIds.clear();
    this.textCells.clear();
    this.dateCells.clear();
    this.copying = null;
    this.copied = null;
    // Dedupe keeps a notice that matches the previous line. A new workbook
    // must be able to raise that same sentence again.
    this.notices.length = 0;
    this.textLayouts.clear();
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
      if (sheet.textFormat) this.textLayouts.set(sheet.name, { layout: sheet.textFormat, opened: new Set(Object.keys(sheet.cells)) });
      let rows = MIN_ROWS;
      let cols = MIN_COLUMNS;
      const cellData: Record<number, Record<number, ICellData>> = {};
      for (const [address, cell] of Object.entries(sheet.cells)) {
        const { row, col } = fromA1(address);
        rows = Math.max(rows, row + 1 + ROW_MARGIN);
        cols = Math.max(cols, col + 1 + COLUMN_MARGIN);
        const at = `${sheet.name}!${address}`;
        if (cell.text) this.textCells.add(at);
        const value = cell.formula !== undefined ? this.projectScalar(calc[at], at) : cell.value;
        const data = toCellData(value, cell.formula);
        const style = toUniverStyle(cell.style);
        if (style) {
          data.s = style;
          if (style.n) this.formatted.add(at);
          if (style.n && isDateFormat(style.n.pattern)) this.dateCells.add(at);
        }
        (cellData[row] ??= {})[col] = data;
        this.projected.set(at, { value, f: cell.formula });
      }
      const columnData: Record<number, IColumnData> = {};
      for (const [letters, width] of Object.entries(sheet.columnWidths ?? {})) {
        const { col } = fromA1(`${letters}1`);
        cols = Math.max(cols, col + 1);
        columnData[col] = { w: columnWidthPixels(width) };
      }
      data.sheets[id] = { id, name: sheet.name, rowCount: rows, columnCount: cols, cellData, columnData };
    });
    this.projecting = true;
    try {
      if (this.workbook()) this.host.univerAPI.disposeUnit(UNIT_ID);
      this.host.univerAPI.createWorkbook(data);
    } finally {
      this.projecting = false;
    }
    this.watchClipboard();
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
        // A number format makes Univer show text ("12.75%") for the number
        // Forge holds, so only the stored value is compared there.
        if (label === 'displayed' && this.formatted.has(key)) continue;
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
    this.pending += 1;
    const next = this.queue
      .then(task)
      .catch((error) => this.notice(`Something went wrong: ${error instanceof Error ? error.message : String(error)}`))
      .finally(() => {
        this.pending -= 1;
      });
    this.queue = next;
    return next;
  }

  private notice(text: string, kind: NoticeKind = 'error') {
    this.notices.push(text);
    this.onNotice(text, kind);
  }

  /** One-cell projection of a calculated value, with a notice when the array is not 1×1. */
  private projectScalar(value: CalculatedValue | undefined, where: string): Scalar {
    if (value && typeof value === 'object' && 'rows' in value && !isSingleCellArray(value)) {
      this.arrays.set(where, value.rows);
      const columns = Math.max(0, ...value.rows.map((row) => row.length));
      const text = `${where} returns ${value.rows.length} by ${columns} values. The grid shows one value per cell, so the cell stays blank. Select it to preview the result.`;
      if (this.notices.at(-1) !== text) this.notice(text, 'info');
    } else {
      this.arrays.delete(where);
    }
    return scalar(value);
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
      if (e.code === 'nothing_to_undo') return (this.notice('Nothing to undo.', 'info'), false);
      if (e.code === 'nothing_to_redo') return (this.notice('Nothing to redo.', 'info'), false);
      const revisions = e.current_revision ? ` (expected version ${e.expected_revision}, workbook is at version ${e.current_revision})` : '';
      this.notice(`${REJECTED} The ${label} was refused: ${e.message} [${e.code}]${revisions}`);
      this.reprojectAll(op.type === 'set_cells' ? op.edits.map((edit) => `${edit.sheet}!${edit.address}`) : []);
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

  /** Write changed cells into the projection and Univer. */
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
      const value = this.projectScalar(cell.value, `${cell.sheet}!${cell.address}`);
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
    const present = new Set<string>();
    for (const sheet of snapshot.sheets) {
      for (const [address, cell] of Object.entries(sheet.cells)) {
        const key = `${sheet.name}!${address}`;
        present.add(key);
        const value = cell.formula !== undefined ? this.projectScalar(calc[key], key) : cell.value;
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
    // A cell Forge no longer holds (an undone insertion) is absent: blank it.
    for (const key of [...this.projected.keys()]) {
      if (present.has(key)) continue;
      this.projected.delete(key);
      this.arrays.delete(key);
      const { sheetName, address } = splitCellKey(key);
      const { row, col } = fromA1(address);
      const matrix = bySheet.get(sheetName) ?? {};
      (matrix[row] ??= {})[col] = toCellData(null);
      bySheet.set(sheetName, matrix);
    }
    this.write(bySheet);
  }

  /**
   * Restore Univer to the last projection, e.g. after a rejected edit.
   * `alsoClear` names cells the rejected edit typed into that Forge does not
   * hold (for example an out-of-bounds insertion), so their editor text is
   * wiped instead of lingering in the view.
   */
  private reprojectAll(alsoClear: string[] = []) {
    const bySheet = new Map<string, Record<number, Record<number, ICellData>>>();
    const put = (key: string, data: ICellData) => {
      const { sheetName, address } = splitCellKey(key);
      const { row, col } = fromA1(address);
      const matrix = bySheet.get(sheetName) ?? {};
      (matrix[row] ??= {})[col] = data;
      bySheet.set(sheetName, matrix);
    };
    for (const [key, cell] of this.projected) put(key, toCellData(cell.value, cell.f));
    for (const key of alsoClear) {
      if (this.projected.has(key)) continue;
      let sheetName: string, row: number, col: number;
      try {
        ({ sheetName } = splitCellKey(key));
        ({ row, col } = fromA1(splitCellKey(key).address));
      } catch {
        continue; // not an address Univer could have shown
      }
      const sheet = this.workbook()?.getSheetBySheetId(this.sheetIds.get(sheetName) ?? '');
      if (!sheet || row >= sheet.getRowCount() || col >= sheet.getColumnCount()) continue;
      put(key, toCellData(null));
    }
    this.write(bySheet);
  }

  private write(bySheet: Map<string, Record<number, Record<number, ICellData>>>) {
    this.projecting = true;
    try {
      for (const [sheet, cellValue] of bySheet) {
        this.makeRoom(sheet, cellValue);
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

  /**
   * Grow a sheet's grid so the blank margin kept past the last held cell at
   * open is still there after cells are written further out (a long paste,
   * or typing near the bottom). The grid never shrinks and stops at Excel's
   * size. Called while projecting.
   */
  private makeRoom(sheetName: string, cellValue: Record<number, Record<number, ICellData>>) {
    const subUnitId = this.sheetIds.get(sheetName);
    const sheet = this.workbook()?.getSheetBySheetId(subUnitId ?? '');
    if (!sheet) return;
    let [lastRow, lastColumn] = [-1, -1];
    for (const [row, cells] of Object.entries(cellValue)) {
      for (const [col, cell] of Object.entries(cells)) {
        if (cell.v == null && !cell.f) continue;
        lastRow = Math.max(lastRow, Number(row));
        lastColumn = Math.max(lastColumn, Number(col));
      }
    }
    const rowCount = Math.min(EXCEL_ROWS, lastRow + 1 + ROW_MARGIN);
    const columnCount = Math.min(EXCEL_COLUMNS, lastColumn + 1 + COLUMN_MARGIN);
    if (lastRow >= 0 && rowCount > sheet.getRowCount()) {
      this.commands.syncExecuteCommand('sheet.mutation.set-worksheet-row-count', { unitId: UNIT_ID, subUnitId, rowCount }, { onlyLocal: true });
    }
    if (lastColumn >= 0 && columnCount > sheet.getColumnCount()) {
      this.commands.syncExecuteCommand('sheet.mutation.set-worksheet-column-count', { unitId: UNIT_ID, subUnitId, columnCount }, { onlyLocal: true });
    }
  }

  private sheetName(subUnitId: string | undefined): string | undefined {
    if (!subUnitId) return this.workbook()?.getActiveSheet()?.getName();
    for (const [name, id] of this.sheetIds) if (id === subUnitId) return name;
    return undefined;
  }

  private intercept(info: Readonly<ICommandInfo>) {
    const id = info.id;
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
    if (id === 'sheet.command.clear-selection-content') {
      const params = (info.params ?? {}) as { subUnitId?: string; ranges?: UniverRange[] };
      this.routeClear(id, this.sheetName(params.subUnitId), params.ranges ?? this.selection());
    }
    if (id === 'sheet.command.auto-clear-content') {
      // The fill handle dragged back over its own selection.
      const params = (info.params ?? {}) as { subUnitId?: string; clearRange?: UniverRange; selectionRange?: UniverRange };
      this.routeClear(id, this.sheetName(params.subUnitId), params.clearRange ? [params.clearRange] : [], params.selectionRange);
    }
    if (id === 'sheet.command.paste-by-short-key') this.routePaste(id, (info.params ?? {}) as Record<string, unknown>);
    if (id === 'sheet.command.auto-fill') this.routeFill(id, (info.params ?? {}) as Record<string, unknown>);
    if (id === 'univer.command.copy') this.noteCopy();
    if (VIEW_COMMANDS.has(id)) return;
    if (GUARDED_PREFIXES.some((prefix) => id.startsWith(prefix))) this.block(id, refusalText(id));
  }

  /** Refuse a command: record its id for the audit and say why in plain words. */
  private block(id: string, reason: string): never {
    this.audit.blocked.push(id);
    this.notice(reason, 'info');
    throw new CustomCommandExecutionError(`${id} blocked`);
  }

  /** Refuse a command after it was taken off Univer's hands: same record, same notice, no throw. */
  private refuse(id: string, reason: string): false {
    this.audit.blocked.push(id);
    this.notice(reason, 'info');
    return false;
  }

  // ---- clear, paste and fill -------------------------------------------
  //
  // Each of these is one command envelope, so one step of undo. They change
  // values and formulas only; formats stay where they are. A copied formula
  // is always Forge's own copy (client.copyFormulas): nothing here moves a
  // reference.

  /** A Univer range inside the sheet's grid. A whole row or column arrives open-ended. */
  private rect(range: UniverRange, sheetName: string): Rect {
    const sheet = this.workbook()?.getSheetBySheetId(this.sheetIds.get(sheetName) ?? '');
    const [lastRow, lastColumn] = [(sheet?.getRowCount() ?? MIN_ROWS) - 1, (sheet?.getColumnCount() ?? MIN_COLUMNS) - 1];
    const at = (value: number, open: number) => (Number.isFinite(value) ? value : open);
    return {
      startRow: at(range.startRow, 0),
      endRow: at(range.endRow, lastRow),
      startColumn: at(range.startColumn, 0),
      endColumn: at(range.endColumn, lastColumn),
    };
  }

  /** The active sheet's selected ranges. */
  private selection(): UniverRange[] {
    const ranges = this.host.univerAPI.getActiveWorkbook()?.getActiveSheet().getSelection()?.getActiveRangeList() ?? [];
    return ranges.map((range) => range.getRange());
  }

  private held(sheet: string, row: number, col: number): Held | undefined {
    return this.projected.get(`${sheet}!${toA1(row, col)}`);
  }

  /** Put the selection on a range of a sheet, when that sheet is still the one shown. */
  private select(sheetName: string, rect: Rect) {
    const sheet = this.host.univerAPI.getActiveWorkbook()?.getActiveSheet();
    if (!sheet || sheet.getSheetId() !== this.sheetIds.get(sheetName)) return;
    sheet.getRange(rect.startRow, rect.startColumn, rows(rect), columns(rect)).activate();
  }

  /** Delete: blank the contents of every cell Forge holds in the ranges, apart from those in `keep`. */
  private routeClear(id: string, sheet: string | undefined, ranges: UniverRange[], selectAfter?: UniverRange, keep?: Rect): never {
    if (!sheet) this.block(id, 'That sheet is not part of this workbook.');
    const rects = ranges.map((range) => this.rect(range, sheet));
    this.audit.routed.push(id);
    void this.enqueue(async () => {
      const prefix = `${sheet}!`;
      const edits: Edit[] = [];
      for (const [key, cell] of this.projected) {
        if (!key.startsWith(prefix) || key.lastIndexOf('!') !== prefix.length - 1 || isBlank(cell)) continue;
        const address = key.slice(prefix.length);
        const { row, col } = fromA1(address);
        if (keep && contains(keep, row, col)) continue;
        if (rects.some((rect) => contains(rect, row, col))) edits.push({ sheet, address, value: null });
      }
      // Nothing held there: nothing to do, and nothing to undo.
      if (edits.length > 0 && !(await this.applyToForge({ type: 'set_cells', edits }, 'clear', { source: 'clear', univer: id }))) return;
      if (selectAfter) this.select(sheet, this.rect(selectAfter, sheet));
    });
    throw new CustomCommandExecutionError(`${id} routed to Forge`);
  }

  /**
   * Record what a copy is about to take. Univer copies the last selection;
   * the snapshot is Forge's cells there now, so a later paste writes what was
   * copied even if the source has changed since.
   */
  private noteCopy() {
    this.watchClipboard();
    this.copying = null;
    const focused = this.instances.getFocusedUnit();
    if (focused && focused.type === UniverInstanceType.UNIVER_DOC) return; // copying text inside the cell editor
    const sheet = this.sheetName(undefined);
    const range = this.selection().at(-1);
    if (!sheet || !range) return;
    const rect = this.rect(range, sheet);
    let cells: Map<string, Held> | null = null;
    if (cellCount(rect) <= MAX_RANGE_CELLS) {
      cells = new Map();
      for (let row = rect.startRow; row <= rect.endRow; row += 1) {
        for (let col = rect.startColumn; col <= rect.endColumn; col += 1) {
          const cell = this.held(sheet, row, col);
          if (cell && !isBlank(cell)) cells.set(`${row},${col}`, { ...cell });
        }
      }
    }
    this.copying = { rect, cells };
  }

  /**
   * Learn the id Univer gives each copy, so a paste can tell this page's own
   * copy (formulas move with it) from anything else on the clipboard (text).
   * Reading the clipboard is switched off: a paste arrives with its content
   * in the browser's paste event, and asking for more would make the browser
   * prompt for clipboard permission. Safe to call again.
   */
  private watchClipboard() {
    if (this.clipboardWatched) return;
    try {
      const clipboard = this.host.univer.__getInjector().get(IClipboardInterfaceService);
      this.clipboardWatched = true;
      const noteWrite = (html: string) => {
        const id = copyIdOf(html);
        this.copied = id && this.copying ? { id, ...this.copying } : null;
        this.copying = null;
      };
      // The service is handed out behind a lazy proxy that remembers each
      // method the first time it is read. So the replacements are assigned
      // without reading the originals first, and the original write is taken
      // from the class when it is needed.
      clipboard.write = function (this: object, text: string, html: string, ...rest: unknown[]) {
        noteWrite(html);
        const original = (Object.getPrototypeOf(this) as { write: (...args: unknown[]) => Promise<void> }).write;
        return original.call(this, text, html, ...rest);
      } as typeof clipboard.write;
      clipboard.read = async () => [];
    } catch {
      // Univer registers the service when its first workbook is created; until
      // then, and in a host without one, every paste is treated as text.
    }
  }

  /** Ctrl+V: this page's own copy keeps its formulas; anything else is pasted as typed text. */
  private routePaste(id: string, params: Record<string, unknown>): never {
    const target = params.target as { subUnitId?: string; range?: UniverRange } | undefined;
    const sheet = this.sheetName(target?.subUnitId);
    const range = target?.range ?? this.selection().at(-1);
    if (!sheet || !range) this.block(id, 'Select a cell to paste into.');
    const html = typeof params.htmlContent === 'string' ? params.htmlContent : '';
    const text = typeof params.textContent === 'string' ? params.textContent : '';
    if (html === '' && text === '') {
      this.block(id, Array.isArray(params.files) && params.files.length > 0 ? 'Pasting pictures and files is not available yet.' : 'There was nothing to paste.');
    }
    const selection = this.rect(range, sheet);
    const copied = this.copied && copyIdOf(html) === this.copied.id ? this.copied : null;
    this.audit.routed.push(id);
    void this.enqueue(async () => {
      let shape: { rows: number; columns: number };
      if (copied) {
        if (!copied.cells) return this.refuse(id, `${REJECTED} That copy is larger than ${MAX_RANGE_CELLS.toLocaleString('en-US')} cells.`);
        const source = copied.rect;
        shape = pasteShape(rows(source), columns(source), selection);
        if (!this.fits(id, selection, shape)) return false;
        const steps: CopyStep[] = [];
        for (let r = 0; r < shape.rows; r += 1) {
          for (let c = 0; c < shape.columns; c += 1) {
            const from = { row: source.startRow + (r % rows(source)), col: source.startColumn + (c % columns(source)) };
            steps.push({ row: selection.startRow + r, col: selection.startColumn + c, from: { ...from, cell: copied.cells.get(`${from.row},${from.col}`) } });
          }
        }
        if (!(await this.writeCopies(id, sheet, steps, 'paste'))) return false;
      } else {
        // Rows are as written, not padded: the size is checked before any cell of the rectangle is built.
        const table = parseClipboardText(text);
        const width = clipboardColumns(table);
        if (table.length === 0 || width === 0) return this.refuse(id, 'There was no text to paste.');
        shape = pasteShape(table.length, width, selection);
        if (!this.fits(id, selection, shape)) return false;
        const edits: Edit[] = [];
        for (let r = 0; r < shape.rows; r += 1) {
          for (let c = 0; c < shape.columns; c += 1) {
            const [row, col] = [selection.startRow + r, selection.startColumn + c];
            const typed = table[r % table.length][c % width] ?? '';
            // Each cell is read exactly as if it had been typed there.
            const input = parseInput({ v: typed }, this.isTextFormat(sheet, row, col)) ?? { value: null };
            if ('value' in input && input.value === null && isBlank(this.held(sheet, row, col))) continue;
            edits.push({ sheet, address: toA1(row, col), ...input });
          }
        }
        if (edits.length > 0 && !(await this.applyToForge({ type: 'set_cells', edits }, 'paste', { source: 'paste', univer: id }))) return false;
      }
      this.select(sheet, { startRow: selection.startRow, endRow: selection.startRow + shape.rows - 1, startColumn: selection.startColumn, endColumn: selection.startColumn + shape.columns - 1 });
      return true;
    });
    throw new CustomCommandExecutionError(`${id} routed to Forge`);
  }

  /** A paste of this shape stays inside Excel's grid and under the size limit, or it is refused. */
  private fits(id: string, selection: Rect, shape: { rows: number; columns: number }): boolean {
    if (selection.startRow + shape.rows > EXCEL_ROWS || selection.startColumn + shape.columns > EXCEL_COLUMNS) {
      return this.refuse(id, `${REJECTED} The paste does not fit on the sheet from there.`);
    }
    if (shape.rows * shape.columns > MAX_RANGE_CELLS) {
      return this.refuse(id, `${REJECTED} That is more than ${MAX_RANGE_CELLS.toLocaleString('en-US')} cells at once.`);
    }
    return true;
  }

  /**
   * The fill handle, Ctrl+D and Ctrl+R: repeat the source over the rest of
   * the target. Ctrl+D and Ctrl+R always copy (`applyType: 'COPY'`). The fill
   * handle copies where Excel would copy and continues the series `planLine`
   * builds (numbers, text ending in a number, day and month names). Any other
   * series Excel would continue is refused, because a copy there would not
   * match Excel.
   * Univer sends the range the handle was dragged to as `targetRange`.
   */
  private routeFill(id: string, params: Record<string, unknown>): never {
    const sheet = this.sheetName(params.subUnitId as string | undefined);
    const [sourceRange, targetRange] = [params.sourceRange as UniverRange | undefined, params.targetRange as UniverRange | undefined];
    if (!sheet || !sourceRange || !targetRange) this.block(id, refusalText('fill-'));
    const alwaysCopy = params.applyType === 'COPY';
    const source = this.rect(sourceRange, sheet);
    const dragged = this.rect(targetRange, sheet);
    if (contains(source, dragged.startRow, dragged.startColumn) && contains(source, dragged.endRow, dragged.endColumn)) {
      // The handle was dragged back over its own selection: Univer's gesture
      // for clearing the cells the selection gave up.
      this.routeClear(id, sheet, [source], dragged, dragged);
    }
    const target = union(source, dragged);
    const down = rows(target) > rows(source);
    this.audit.routed.push(id);
    void this.enqueue(async () => {
      if (cellCount(target) > MAX_RANGE_CELLS) return this.refuse(id, `${REJECTED} That is more than ${MAX_RANGE_CELLS.toLocaleString('en-US')} cells at once.`);
      // One line per column when filling down or up, per row when filling across.
      const length = down ? rows(source) : columns(source);
      const plans: LinePlan[] = [];
      if (!alwaysCopy) {
        const lines = down ? columns(source) : rows(source);
        for (let line = 0; line < lines; line += 1) {
          const at = (i: number) => (down ? { row: source.startRow + i, col: source.startColumn + line } : { row: source.startRow + line, col: source.startColumn + i });
          const cells = Array.from({ length }, (_, i) => this.held(sheet, at(i).row, at(i).col));
          const dates = Array.from({ length }, (_, i) => this.dateCells.has(`${sheet}!${toA1(at(i).row, at(i).col)}`));
          const plan = planLine(cells, dates);
          if (plan.kind === 'refuse') return this.refuse(id, SERIES_REFUSED);
          plans.push(plan);
        }
      }
      const steps: CopyStep[] = [];
      for (let row = target.startRow; row <= target.endRow; row += 1) {
        for (let col = target.startColumn; col <= target.endColumn; col += 1) {
          if (contains(source, row, col)) continue;
          const plan = plans[down ? col - source.startColumn : row - source.startRow];
          if (plan?.kind === 'series') {
            const value = plan.at(down ? row - source.startRow : col - source.startColumn);
            if (value === undefined) return this.refuse(id, `${REJECTED} The series cannot be continued as far as ${toA1(row, col)}.`);
            steps.push({ row, col, from: { row, col, cell: { value } } });
            continue;
          }
          const from = fillSource(source, row, col);
          steps.push({ row, col, from: { ...from, cell: this.held(sheet, from.row, from.col) } });
        }
      }
      if (!(await this.writeCopies(id, sheet, steps, 'fill'))) return false;
      this.select(sheet, target);
      return true;
    });
    throw new CustomCommandExecutionError(`${id} routed to Forge`);
  }

  /**
   * Write copies of cells as one edit. A value is copied as it is. A formula
   * is what Forge says it becomes at its new place. A blank source clears a
   * cell that holds something and leaves an empty one alone. If any formula
   * cannot be copied, nothing is written.
   */
  private async writeCopies(id: string, sheet: string, steps: CopyStep[], label: 'paste' | 'fill'): Promise<boolean> {
    const edits: (Edit | null)[] = [];
    const asked: { at: number; step: CopyStep }[] = [];
    for (const step of steps) {
      const address = toA1(step.row, step.col);
      const cell = step.from.cell;
      if (isBlank(cell)) {
        if (!isBlank(this.held(sheet, step.row, step.col))) edits.push({ sheet, address, value: null });
      } else if (cell!.f === undefined) {
        edits.push({ sheet, address, value: cell!.value });
      } else if (step.row === step.from.row && step.col === step.from.col) {
        edits.push({ sheet, address, formula: cell!.f });
      } else {
        asked.push({ at: edits.length, step });
        edits.push(null);
      }
    }
    if (asked.length > 0) {
      const answer = await this.client.copyFormulas(
        asked.map(({ step }) => ({ formula: step.from.cell!.f!, rowDelta: step.row - step.from.row, columnDelta: step.col - step.from.col })),
      );
      if (!answer.ok) return this.refuse(id, `${REJECTED} The ${label} was refused: ${answer.error.message} [${answer.error.code}]`);
      for (const [index, { at, step }] of asked.entries()) {
        const result = answer.value.formulas[index];
        if (!result || 'error' in result) {
          const from = toA1(step.from.row, step.from.col);
          const why = !result
            ? 'no answer from the engine'
            : result.error.code === 'invalid_reference'
              ? `at ${toA1(step.row, step.col)} it would point outside the sheet`
              : `${result.error.message} [${result.error.code}]`;
          return this.refuse(id, `${REJECTED} The formula in ${from} cannot be copied: ${why}.`);
        }
        edits[at] = { sheet, address: toA1(step.row, step.col), formula: result.formula };
      }
    }
    const ready = edits.filter((edit): edit is Edit => edit !== null);
    if (ready.length === 0) return true;
    return this.applyToForge({ type: 'set_cells', edits: ready }, label, { source: label, univer: id });
  }

  /** SetRangeValuesCommand params -> Forge edits, or a reason to refuse. */
  private translateSetRangeValues(params: Record<string, unknown>): Edit[] | string {
    const sheet = this.sheetName(params.subUnitId as string | undefined);
    if (!sheet) return 'That sheet is not part of this workbook.';
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
    if (cells.length === 0) return 'There was nothing to change.';
    const edits: Edit[] = [];
    for (const { row, col, cell } of cells) {
      const input = parseInput(cell ?? {}, this.isTextFormat(sheet, row, col) || this.editsForcedText(sheet, row, col, cell));
      if (!input) return refusalText('style');
      edits.push({ sheet, address: toA1(row, col), ...input });
    }
    return edits;
  }

  /**
   * An edit made in place (Univer sends it as rich text with no value) to a
   * cell holding text that would not be text if typed afresh: `007` or `=A1`
   * entered with a leading apostrophe. Univer's editor shows such a cell
   * without the apostrophe, so the edited entry arrives without it too. The
   * entry stays text, as the cell was; reading `0078` as a number would drop
   * the zeros the apostrophe was typed to keep.
   */
  private editsForcedText(sheet: string, row: number, col: number, cell: ICellData | null | undefined): boolean {
    if (!cell || (cell.v !== null && cell.v !== undefined) || !cell.p?.body?.dataStream) return false;
    const held = this.held(sheet, row, col);
    if (!held || held.f || typeof held.value !== 'string') return false;
    const afresh = parseInput({ v: held.value });
    return !(afresh && 'value' in afresh && afresh.value === held.value);
  }

  /** Typed input here stays text: the cell is Text (`@`), or inherits it. */
  isTextFormat(sheet: string, row: number, col: number): boolean {
    const address = toA1(row, col);
    if (this.textCells.has(`${sheet}!${address}`)) return true;
    const entry = this.textLayouts.get(sheet);
    if (!entry || entry.opened.has(address)) return false;
    const [r, c] = [row + 1, col + 1];
    if (entry.layout.rows.includes(r)) return true;
    if (entry.layout.generalRows.includes(r)) return false;
    return entry.layout.columns.some(([first, last]) => first <= c && c <= last);
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
