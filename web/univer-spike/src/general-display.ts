// Draws General-format numbers the way Excel's General format does when a
// number is too long for its cell: rounded decimals, not scientific notation.
// The rule is in ./general-number.ts; this file only fits it into Univer.
//
// Univer 1.2.0 decides the drawn text of a number cell in
// SpreadsheetSkeleton._applyNumberDisplay (@univerjs/engine-render). For a
// General cell whose text is too wide it tries scientific patterns only
// (0E+00, 0.#E+00, ...), so 0.08663094803653162 in an 88px column is drawn as
// 8.6631E-02. It has no setting and no public hook for that step, and the
// only public hook near it (the CELL_CONTENT interceptor) would change what
// every reader of the cell gets, a copy included. So the private method is
// wrapped: Univer's own step runs, then the drawn text of a General number
// cell is replaced. Nothing else is touched: the cell's value, the formula
// bar, a copy and an export never pass through here.
//
// The number that is rounded is the one Univer shows for the cell, which is
// also what its formula bar shows: Forge's number with floating-point noise
// trimmed by Univer (at most 15 significant digits).
//
// `_applyNumberDisplay` is private to Univer 1.2.0. If an upgrade renames or
// removes it, `installGeneralNumberDisplay` returns false and the "general
// numbers" acceptance test fails.

import { BooleanNumber, CellValueType, isDefaultFormat, type IStyleData } from '@univerjs/core';
import { DEFAULT_PADDING_DATA, FontCache, SpreadsheetSkeleton, type IFontCacheItem } from '@univerjs/engine-render';
import { generalNumberText, overflowText } from './general-number';

type ApplyNumberDisplay = (this: SpreadsheetSkeleton, row: number, col: number, fontCache: IFontCacheItem, style: IStyleData) => void;
/** Univer's step, marked once it has been wrapped so it is never wrapped twice. */
type Wrappable = ApplyNumberDisplay & { forgeGeneralNumbers?: boolean };
interface SkeletonInternals {
  _applyNumberDisplay?: Wrappable;
}

/** The glyph Univer keeps clear beside a General number; the same clearance is kept here. */
const CLEARANCE_GLYPH = '0';

/** The width a number has to be drawn in, measured the way Univer measures it for the same step. */
function innerWidth(skeleton: SpreadsheetSkeleton, row: number, col: number, fontCache: IFontCacheItem, style: IStyleData): number {
  const cell = skeleton.getCellWithCoordByIndex(row, col, false);
  const { startX, endX } = cell.isMergedMainCell ? cell.mergeInfo : cell;
  const padding = style.pd ?? DEFAULT_PADDING_DATA;
  const extension = fontCache.cellData?.fontRenderExtension;
  return (
    endX - startX - (padding.l ?? DEFAULT_PADDING_DATA.l) - (padding.r ?? DEFAULT_PADDING_DATA.r) - (extension?.leftOffset ?? 0) - (extension?.rightOffset ?? 0)
  );
}

function redraw(skeleton: SpreadsheetSkeleton, row: number, col: number, fontCache: IFontCacheItem, style: IStyleData) {
  const cell = fontCache.cellData;
  // The cells Univer treats as General numbers, and only those holding a real number.
  if (!cell || style.stf === BooleanNumber.TRUE || fontCache.documentSkeleton) return;
  if (cell.t !== CellValueType.NUMBER && ((cell.t !== undefined && cell.t !== null) || typeof cell.v !== 'number')) return;
  if (!isDefaultFormat(style.n?.pattern)) return;
  const value = numberOf(cell.v);
  if (value === null) return;
  const available = innerWidth(skeleton, row, col, fontCache, style);
  if (available <= 0) return;
  const width = (text: string) => FontCache.getMeasureText(text, fontCache.fontString).width;
  const clearance = width(CLEARANCE_GLYPH);
  fontCache.displayText = generalNumberText(value, (text) => width(text) + clearance <= available) ?? overflowText(available, width('#'));
}

/**
 * The number a General cell shows, or null when the cell holds anything else.
 * Univer hands the renderer a very small or very large number as the text
 * JavaScript prints for it in capitals ("1.23E-7"); that text, and only that
 * text, is read back as its number.
 */
function numberOf(shown: unknown): number | null {
  if (typeof shown === 'number') return Number.isFinite(shown) ? shown : null;
  if (typeof shown !== 'string' || !shown.includes('E')) return null;
  const value = Number(shown);
  return Number.isFinite(value) && String(value).toUpperCase() === shown ? value : null;
}

/**
 * Make every Univer sheet on the page draw General numbers by the rule in
 * ./general-number.ts. Safe to call more than once. Returns false when
 * Univer no longer has the step this replaces, in which case nothing changes
 * and Univer's own drawing stays.
 */
export function installGeneralNumberDisplay(): boolean {
  const internals = SpreadsheetSkeleton.prototype as unknown as SkeletonInternals;
  const original = internals._applyNumberDisplay;
  if (typeof original !== 'function') return false;
  if (original.forgeGeneralNumbers) return true;
  const wrapped: Wrappable = function (row, col, fontCache, style) {
    original.call(this, row, col, fontCache, style);
    try {
      redraw(this, row, col, fontCache, style);
    } catch {
      // This runs for every cell that is drawn. If Univer's internals have
      // shifted under it, the cell keeps the text Univer chose rather than
      // the grid failing to draw; the acceptance test reports the difference.
    }
  };
  wrapped.forgeGeneralNumbers = true;
  internals._applyNumberDisplay = wrapped;
  return true;
}
