// Phone and tablet use: tap to select without the on-screen keyboard, three
// deliberate ways to start typing, swipe to scroll, pinch to zoom the grid,
// and a layout that follows the keyboard. docs/specs/univer-spike.md ("Touch
// and phone use") has the reasons, the sources and what is not verified.
//
// Univer 1.2.0's desktop plugins (the ones this app registers) treat a finger
// as a mouse: a swipe drags out a selection, a double tap does nothing, and
// every tap focuses a hidden editable element, which opens the on-screen
// keyboard. Univer ships a separate mobile plugin set, but it replaces the
// whole interface and has to be chosen before Univer starts, so it cannot
// serve a laptop that has both a touch screen and a mouse. This file leaves
// the desktop plugins in place and changes only what a finger does.
//
// What is used from Univer:
//   public  the facade (startEditing, isCellEditing, zoom, getScrollState,
//           getSkeleton), the commands sheet.command.set-scroll-relative and
//           sheet.command.scroll-to-cell, the CommandExecuted event, and
//           IEditorBridgeService.refreshEditCellPosition and
//           SheetCellEditorResizeService.fitTextSize (both exported by
//           @univerjs/sheets-ui; the adapter already uses the first)
//   private the ids of the two hidden editors, the id prefix of the grid
//           canvas, the data-u-comp names and the univer-h-7 class of the
//           formula bar, and the fact that the grid reads pointer events
//           from its canvas. `state()` reports whether each was found; the
//           touch acceptance tests fail when an upgrade moves them.

import { IEditorBridgeService, SheetCellEditorResizeService } from '@univerjs/sheets-ui';

import type { UniverHost } from './univer-host';

/** Where the shell uses touch sizes. The same query gates them in shell.css. */
export const TOUCH_QUERY = '(pointer: coarse)';

/**
 * Grid zoom a touch device starts at. 1 is 100 percent, the same as a mouse
 * device: see "Default zoom" in the spec for why. This is the one place to
 * change it.
 */
export const TOUCH_DEFAULT_ZOOM: number = 1;

/** A finger that moves less than this (CSS px) is a tap, not a swipe. */
const TAP_SLOP = 10;
/** Two taps on the same cell this close in time (ms) are a double tap. */
const DOUBLE_TAP_MS = 350;
/** A finger held still this long (ms) starts a range selection. */
const LONG_PRESS_MS = 450;
/** Pinch limits for the grid zoom. */
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
/** While typing, a visible area shorter than this (CSS px) hides the app bar and status bar. */
const SHORT_VIEW = 520;
/** Later checks (ms) that the cell being edited is still in view after the visible area changed. */
const REVEAL_AFTER_MS = [150, 450] as const;
/** A tap this close (CSS px) above or below the formula bar text still starts an edit there. */
const FORMULA_ROW_PAD = 14;

const EDITOR_IDS = ['__editor___INTERNAL_EDITOR__DOCS_NORMAL', '__editor___INTERNAL_EDITOR__DOCS_FORMULA_BAR'] as const;
const GRID_CANVAS_PREFIX = 'univer-sheet-main-canvas';
const EDIT_VISIBLE = 'sheet.operation.set-cell-edit-visible';

export interface TouchState {
  /** The last pointer used was a finger (or the device's main pointer is one and none was used yet). */
  touch: boolean;
  editing: boolean;
  /** Both hidden editors were found by id. */
  editors: boolean;
  /** The grid canvas was found by id prefix. */
  canvas: boolean;
  /** The Edit button sits in Univer's formula bar. */
  editButton: boolean;
  /** CSS px of the page hidden at the bottom, by an on-screen keyboard for example. */
  keyboardInset: number;
  /** The app bar and status bar are hidden to leave room while typing. */
  compact: boolean;
}

export interface TouchSupport {
  state(): TouchState;
  /** Call after a workbook is loaded: applies the touch default zoom. */
  workbookLoaded(): void;
}

interface Point {
  x: number;
  y: number;
}

interface Gesture {
  id: number;
  canvas: HTMLCanvasElement;
  start: Point;
  last: Point;
  mode: 'pending' | 'pan' | 'select' | 'dead';
  timer: number;
  samples: { t: number; x: number; y: number }[];
}

export function installTouch(host: UniverHost, container: HTMLElement): TouchSupport {
  const root = document.documentElement;
  const coarse = matchMedia(TOUCH_QUERY);
  let touch = coarse.matches;
  let editing = false;

  const workbook = () => host.univerAPI.getActiveWorkbook();
  /**
   * The cell an edit belongs to, read when the edit starts. While a formula
   * is typed the selection moves to the cells it refers to, so it cannot be
   * read later.
   */
  let editCell: { sheetId: string; row: number; column: number } | null = null;
  const currentCell = () => {
    const sheet = workbook()?.getActiveSheet();
    const cell = sheet?.getSelection()?.getCurrentCell();
    return sheet && cell ? { sheetId: sheet.getSheetId(), row: cell.actualRow, column: cell.actualColumn } : null;
  };
  /**
   * Whether the whole cell is on screen, worked out from the scroll position
   * and the size of the grid's box. Univer's own visible range is not used:
   * it changes a few frames after a scroll or a resize.
   */
  function cellInView(cell: { sheetId: string; row: number; column: number }) {
    const sheet = workbook()?.getActiveSheet();
    const canvas = gridCanvas();
    const skeleton = sheet?.getSkeleton();
    if (!sheet || !canvas || !skeleton || sheet.getSheetId() !== cell.sheetId) return false;
    const box = (canvas.parentElement ?? canvas).getBoundingClientRect();
    const zoom = sheet.getZoom();
    const scroll = sheet.getScrollState();
    const first = skeleton.getCellWithCoordByIndex(scroll.sheetViewStartRow, scroll.sheetViewStartColumn);
    const at = skeleton.getCellWithCoordByIndex(cell.row, cell.column);
    const top = skeleton.columnHeaderHeight + at.startY - first.startY - scroll.offsetY;
    const left = skeleton.rowHeaderWidth + at.startX - first.startX - scroll.offsetX;
    return (
      top >= skeleton.columnHeaderHeight - 0.5 &&
      left >= skeleton.rowHeaderWidth - 0.5 &&
      (top + at.endY - at.startY) * zoom <= box.height + 0.5 &&
      (left + at.endX - at.startX) * zoom <= box.width + 0.5
    );
  }

  /**
   * Univer's desktop cell editor stays where it was when the grid scrolls
   * under it. After a scroll made here, ask Univer to work out the cell's
   * place again and move the editor there (the calls its own code makes
   * after it scrolls to a cell). A cell scrolled out of view keeps its
   * editor where it last was, so the editor never lands on the bars.
   */
  const placeEditor = () => {
    if (!editing || !editCell || !cellInView(editCell)) return;
    const injector = host.univer.__getInjector();
    injector.get(IEditorBridgeService).refreshEditCellPosition(false);
    injector.get(SheetCellEditorResizeService).fitTextSize();
  };
  const editors = () => EDITOR_IDS.map((id) => document.getElementById(id)).filter((e): e is HTMLElement => e !== null);
  const gridCanvas = () => container.querySelector<HTMLCanvasElement>(`canvas[id^="${GRID_CANVAS_PREFIX}"]`);
  const isGridCanvas = (target: EventTarget | null): target is HTMLCanvasElement =>
    target instanceof HTMLCanvasElement && target.id.startsWith(GRID_CANVAS_PREFIX);

  /** A finger event for Univer, made here; it is not trusted, so this file's own listeners skip it. */
  const pointerEvent = (type: 'pointerdown' | 'pointermove' | 'pointerup', pointerId: number, at: Point) =>
    new PointerEvent(type, {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
      clientX: at.x,
      clientY: at.y,
      screenX: at.x,
      screenY: at.y,
      pointerId,
      pointerType: 'touch',
      isPrimary: true,
      button: type === 'pointermove' ? -1 : 0,
      buttons: type === 'pointerup' ? 0 : 1,
    });

  // ---------- Keyboard: closed while selecting, open while typing ----------

  /**
   * `inputmode="none"` on the focused editor keeps the on-screen keyboard
   * closed and leaves a hardware keyboard working. Only while the last
   * pointer was a finger: after a mouse click the attribute is removed, so a
   * mouse and keyboard session is exactly as before.
   */
  function paint() {
    root.dataset.touchInput = touch ? 'on' : 'off';
    root.dataset.editing = String(editing);
    for (const editor of editors()) {
      if (touch) editor.setAttribute('inputmode', editing ? 'text' : 'none');
      else editor.removeAttribute('inputmode');
    }
    ensureEditButton();
    fit();
  }

  /**
   * Make the browser look at `inputmode` again. A phone decides whether to
   * show its keyboard when an element takes focus, so the editor gives focus
   * up and takes it back. Univer keeps its edit state through this (the
   * "double tap" acceptance test types after it).
   */
  function refocus(preferred?: HTMLElement | null) {
    const active = document.activeElement;
    const target = preferred ?? editors().find((editor) => editor === active) ?? null;
    if (!target) return;
    target.blur();
    target.focus({ preventScroll: true });
  }

  /**
   * Start typing in the selected cell. Must run inside the tap's own event
   * handler: a phone opens its keyboard only for a focus made there.
   */
  function beginTyping() {
    const book = workbook();
    if (!book) return;
    if (!book.isCellEditing()) book.startEditing();
    editing = book.isCellEditing();
    if (editing) editCell ??= currentCell();
    paint();
    if (editing) refocus(document.getElementById(EDITOR_IDS[0]));
  }

  host.univerAPI.addEvent(host.univerAPI.Event.CommandExecuted, (event) => {
    if (event.id !== EDIT_VISIBLE) return;
    const visible = (event.params as { visible?: boolean } | undefined)?.visible === true;
    if (visible === editing) return;
    editing = visible;
    editCell = visible ? currentCell() : null;
    paint();
    // Typing is over: close the keyboard, keep the focus for hardware keys.
    if (!editing && touch) window.setTimeout(() => !editing && refocus(), 0);
  });

  // The last pointer decides. A pen counts as whatever came before it.
  window.addEventListener(
    'pointerdown',
    (event) => {
      const next = event.pointerType === 'touch' ? true : event.pointerType === 'mouse' ? false : touch;
      if (next === touch) return;
      touch = next;
      paint();
    },
    true,
  );
  coarse.addEventListener('change', () => {
    touch = coarse.matches;
    paint();
  });
  // An editor Univer creates later starts in the right state.
  container.addEventListener('focusin', (event) => {
    const target = event.target as HTMLElement | null;
    if (touch && target && (EDITOR_IDS as readonly string[]).includes(target.id) && !target.hasAttribute('inputmode')) paint();
  });

  // ---------- The Edit button and the formula bar ----------

  const editButton = document.createElement('button');
  editButton.type = 'button';
  editButton.id = 'touch-edit';
  editButton.className = 'touch-edit';
  editButton.title = 'Edit this cell';
  editButton.setAttribute('aria-label', 'Edit this cell');
  editButton.innerHTML =
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 13.5 3 10.8l7.6-7.6a1.4 1.4 0 0 1 2 0l.2.2a1.4 1.4 0 0 1 0 2L5.2 13l-2.7.5Zm7-9.2 2.2 2.2" /></svg><span>Edit</span>';
  // Keep the focus where it is; the click below moves it on purpose.
  editButton.addEventListener('pointerdown', (event) => event.preventDefault());
  editButton.addEventListener('click', beginTyping);

  /** Univer draws the formula bar and may draw it again; put the button back when it does. */
  function ensureEditButton() {
    const actions = container.querySelector('[data-u-comp="formula-bar-actions"]');
    if (actions && editButton.parentElement !== actions) actions.prepend(editButton);
  }
  new MutationObserver(() => {
    ensureEditButton();
    if (touch && editors().some((editor) => !editor.hasAttribute('inputmode'))) paint();
  }).observe(container, { childList: true, subtree: true });

  // A tap in the formula bar starts an edit there (Univer's own handling, on
  // pointer down on the canvas that draws the text). When the finger lifts,
  // open the keyboard for it. A tap that lands in the padding around that
  // canvas is passed on to it, so the whole row takes a tap.
  let formulaBarTap: { wasEditing: boolean } | null = null;
  container.addEventListener(
    'pointerdown',
    (event) => {
      if (!event.isTrusted) return;
      formulaBarTap = null;
      if (event.pointerType !== 'touch' || !(event.target instanceof Element)) return;
      const text = container.querySelector<HTMLCanvasElement>('[data-u-comp="formula-bar"] [data-u-comp="formula-editor"] canvas');
      if (!text) return;
      if (text.contains(event.target)) {
        formulaBarTap = { wasEditing: editing };
        return;
      }
      if (!event.target.closest('[data-u-comp="formula-bar"]')) return;
      if (event.target.closest('[data-u-comp="defined-name"], [data-u-comp="formula-bar-actions"]')) return;
      const box = text.getBoundingClientRect();
      const { clientX: x, clientY: y } = event;
      if (x > box.right || y < box.top - FORMULA_ROW_PAD || y > box.bottom + FORMULA_ROW_PAD) return;
      formulaBarTap = { wasEditing: editing };
      const at = { x: Math.max(x, box.left + 1), y: Math.min(Math.max(y, box.top + 1), box.bottom - 1) };
      text.dispatchEvent(pointerEvent('pointerdown', event.pointerId, at));
      text.dispatchEvent(pointerEvent('pointerup', event.pointerId, at));
    },
    true,
  );
  container.addEventListener(
    'pointerup',
    (event) => {
      if (!event.isTrusted) return;
      const tap = formulaBarTap;
      formulaBarTap = null;
      if (!tap || event.pointerType !== 'touch') return;
      editing = workbook()?.isCellEditing() ?? editing;
      if (editing) editCell ??= currentCell();
      paint();
      if (editing && !tap.wasEditing) refocus(document.getElementById(EDITOR_IDS[1]));
    },
    true,
  );

  // ---------- Fingers on the grid ----------
  //
  // Finger events on the grid canvas are held back from Univer (capture
  // phase) until it is clear what the finger is doing:
  //   lifts without moving      a tap: Univer gets a pointer down and up at
  //                             that point and selects with its own hit test
  //   second tap, same place    start typing
  //   moves                     scroll the grid, with a short glide
  //   held still, then moves    Univer gets the whole drag: range selection
  //                             and the fill handle
  //   two fingers               zoom the grid
  // Mouse and pen events are never touched.

  const fingers = new Map<number, Point>();
  let gesture: Gesture | null = null;
  let pinch: { distance: number; zoom: number } | null = null;
  let lastTap: { t: number; cell: { sheetId: string; row: number; column: number } } | null = null;
  let forwarding = false;

  function forward(type: 'pointerdown' | 'pointermove' | 'pointerup', g: Gesture, at: Point) {
    forwarding = true;
    try {
      g.canvas.dispatchEvent(pointerEvent(type, g.id, at));
    } finally {
      forwarding = false;
    }
  }

  const sheetZoom = () => workbook()?.getActiveSheet().getZoom() ?? 1;

  // Scroll requests are gathered and sent once per frame.
  let owed: Point = { x: 0, y: 0 };
  let scrollFrame = 0;
  function scrollBy(dx: number, dy: number) {
    owed = { x: owed.x + dx, y: owed.y + dy };
    if (scrollFrame) return;
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = 0;
      const { x, y } = owed;
      owed = { x: 0, y: 0 };
      if (x === 0 && y === 0) return;
      // Univer scrolls in sheet units; the finger moves in screen pixels.
      const zoom = sheetZoom();
      void Promise.resolve(host.univerAPI.executeCommand('sheet.command.set-scroll-relative', { offsetX: x / zoom, offsetY: y / zoom })).then(placeEditor);
    });
  }

  let glideFrame = 0;
  function stopGlide() {
    cancelAnimationFrame(glideFrame);
    glideFrame = 0;
  }
  /** Keep scrolling after the finger lifts, slowing down. Speeds are CSS px per ms. */
  function glide(vx: number, vy: number) {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    let previous = performance.now();
    const step = (now: number) => {
      const elapsed = Math.min(now - previous, 34);
      previous = now;
      const slow = 0.996 ** elapsed;
      vx *= slow;
      vy *= slow;
      scrollBy(vx * elapsed, vy * elapsed);
      glideFrame = Math.hypot(vx, vy) > 0.03 ? requestAnimationFrame(step) : 0;
    };
    glideFrame = requestAnimationFrame(step);
  }

  /** True when the point is on a cell, not on the row or column headers. */
  function onCells(canvas: HTMLCanvasElement, at: Point) {
    const sheet = workbook()?.getActiveSheet();
    const skeleton = sheet?.getSkeleton();
    if (!sheet || !skeleton) return false;
    const rect = canvas.getBoundingClientRect();
    const zoom = sheet.getZoom();
    return at.x - rect.left > skeleton.rowHeaderWidth * zoom && at.y - rect.top > skeleton.columnHeaderHeight * zoom;
  }

  function endGesture() {
    if (!gesture) return;
    clearTimeout(gesture.timer);
    gesture = null;
  }

  function fingerDistance() {
    const [a, b] = [...fingers.values()];
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  container.addEventListener(
    'pointerdown',
    (event) => {
      if (forwarding || event.pointerType !== 'touch' || !isGridCanvas(event.target)) return;
      event.stopPropagation();
      stopGlide();
      const at = { x: event.clientX, y: event.clientY };
      fingers.set(event.pointerId, at);
      if (fingers.size === 2) {
        // A second finger: whatever the first was doing, this is a pinch.
        if (gesture?.mode === 'select') forward('pointerup', gesture, gesture.last);
        endGesture();
        pinch = { distance: fingerDistance(), zoom: sheetZoom() };
        return;
      }
      if (fingers.size > 2) return;
      const g: Gesture = { id: event.pointerId, canvas: event.target, start: at, last: at, mode: 'pending', timer: 0, samples: [] };
      g.timer = window.setTimeout(() => {
        if (gesture !== g || g.mode !== 'pending') return;
        g.mode = 'select';
        forward('pointerdown', g, g.start);
      }, LONG_PRESS_MS);
      gesture = g;
    },
    true,
  );

  container.addEventListener(
    'pointermove',
    (event) => {
      if (forwarding || !fingers.has(event.pointerId)) return;
      event.stopPropagation();
      const at = { x: event.clientX, y: event.clientY };
      fingers.set(event.pointerId, at);
      if (pinch && fingers.size === 2) {
        const next = Math.round(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, (pinch.zoom * fingerDistance()) / Math.max(pinch.distance, 1))) * 20) / 20;
        const sheet = workbook()?.getActiveSheet();
        if (sheet && next !== sheet.getZoom()) sheet.zoom(next);
        return;
      }
      const g = gesture;
      if (!g || g.id !== event.pointerId) return;
      if (g.mode === 'pending' && Math.hypot(at.x - g.start.x, at.y - g.start.y) > TAP_SLOP) {
        clearTimeout(g.timer);
        g.mode = 'pan';
        g.last = at;
        return;
      }
      if (g.mode === 'pan') {
        scrollBy(g.last.x - at.x, g.last.y - at.y);
        g.samples.push({ t: event.timeStamp, x: at.x, y: at.y });
        if (g.samples.length > 8) g.samples.shift();
      } else if (g.mode === 'select') {
        forward('pointermove', g, at);
      }
      g.last = at;
    },
    true,
  );

  const lift = (event: PointerEvent) => {
    if (forwarding || !fingers.has(event.pointerId)) return;
    event.stopPropagation();
    fingers.delete(event.pointerId);
    if (pinch) {
      // The finger that stays down after a pinch does nothing more.
      if (fingers.size === 0) pinch = null;
      return;
    }
    const g = gesture;
    if (!g || g.id !== event.pointerId) return;
    endGesture();
    const at = { x: event.clientX, y: event.clientY };
    const cancelled = event.type === 'pointercancel';
    if (g.mode === 'select') {
      forward('pointerup', g, cancelled ? g.last : at);
    } else if (g.mode === 'pan' && !cancelled) {
      const recent = g.samples.filter((sample) => event.timeStamp - sample.t < 120);
      const first = recent[0];
      const span = first ? event.timeStamp - first.t : 0;
      if (first && span > 16) glide((first.x - at.x) / span, (first.y - at.y) / span);
    } else if (g.mode === 'pending' && !cancelled) {
      forward('pointerdown', g, g.start);
      forward('pointerup', g, g.start);
      // Univer has selected by now, so the two taps can be compared by the cell they chose.
      const cell = onCells(g.canvas, g.start) ? currentCell() : null;
      const again =
        cell &&
        lastTap &&
        event.timeStamp - lastTap.t < DOUBLE_TAP_MS &&
        lastTap.cell.sheetId === cell.sheetId &&
        lastTap.cell.row === cell.row &&
        lastTap.cell.column === cell.column;
      if (again) {
        lastTap = null;
        beginTyping();
      } else {
        lastTap = cell ? { t: event.timeStamp, cell } : null;
      }
    }
  };
  container.addEventListener('pointerup', lift, true);
  container.addEventListener('pointercancel', lift, true);
  // A long press on the grid selects; it does not ask for the browser's menu.
  container.addEventListener('contextmenu', (event) => {
    if (touch && isGridCanvas(event.target)) event.preventDefault();
  });

  // ---------- Layout that follows the keyboard ----------
  //
  // Chrome on Android shrinks the page itself (`interactive-widget=
  // resizes-content` in index.html), and the shell's 100dvh follows. Safari
  // on iOS leaves the page as it is and shrinks only the visual viewport, so
  // the shell is sized and placed to the visual viewport here. Nothing is
  // done while the page is pinch-zoomed (scale other than 1): that also
  // changes the visual viewport, and the reader chose it.

  let keyboardInset = 0;
  let compact = false;
  let placement = '';
  let revealFrame = 0;
  let revealTimers: number[] = [];

  function fit() {
    const viewport = window.visualViewport;
    const layoutHeight = root.clientHeight;
    const covered = !!viewport && Math.abs(viewport.scale - 1) < 0.01 && layoutHeight - viewport.height > 1;
    const visible = covered ? viewport.height : layoutHeight;
    const top = covered ? Math.max(0, viewport.offsetTop) : 0;
    const nextCompact = touch && editing && visible < SHORT_VIEW;
    // The visible box: its size and where it starts. Chrome on Android
    // shrinks the page for its keyboard, so nothing is covered and, on a
    // tablet that stays taller than SHORT_VIEW, compact mode does not change
    // either. The size is then the only sign that the grid got smaller.
    const width = covered ? viewport.width : root.clientWidth;
    const placed = `${width}x${visible}+${top}`;
    const changed = placed !== placement || nextCompact !== compact;
    placement = placed;
    keyboardInset = Math.max(0, Math.round(layoutHeight - visible - top));
    compact = nextCompact;
    if (covered) {
      root.style.setProperty('--visible-height', `${visible}px`);
      root.style.setProperty('--visible-top', `${top}px`);
      root.style.setProperty('--keyboard-inset', `${keyboardInset}px`);
      // The page itself must not be left scrolled behind the keyboard.
      if (window.scrollX !== 0 || window.scrollY !== 0) window.scrollTo(0, 0);
    } else {
      root.style.removeProperty('--visible-height');
      root.style.removeProperty('--visible-top');
      root.style.removeProperty('--keyboard-inset');
    }
    root.dataset.typing = compact ? 'compact' : 'off';
    // Only for a finger or a covered page. With a mouse, a window resized
    // while a cell is being edited is left to Univer, as before.
    if (changed && editing && (touch || covered)) revealSoon();
  }

  /** Scroll the cell being edited back into view if the smaller grid has hidden it. */
  function reveal() {
    const sheet = workbook()?.getActiveSheet();
    if (!editing || !sheet || !editCell || sheet.getSheetId() !== editCell.sheetId) return;
    if (cellInView(editCell)) {
      placeEditor();
      return;
    }
    const { row, column } = editCell;
    void Promise.resolve(
      host.univerAPI.executeCommand('sheet.command.scroll-to-cell', {
        range: { startRow: row, endRow: Math.min(row + 1, sheet.getMaxRows() - 1), startColumn: column, endColumn: column },
      }),
    ).then(placeEditor);
  }

  /**
   * Univer resizes its canvas a few frames after the shell changes size, so
   * the check runs after two frames and again at each delay in REVEAL_AFTER_MS.
   * A check that finds the cell in view only puts the editor back on it.
   */
  function revealSoon() {
    cancelAnimationFrame(revealFrame);
    for (const timer of revealTimers) clearTimeout(timer);
    revealFrame = requestAnimationFrame(() => {
      revealFrame = requestAnimationFrame(() => {
        revealFrame = 0;
        reveal();
      });
    });
    revealTimers = REVEAL_AFTER_MS.map((delay) => window.setTimeout(reveal, delay));
  }

  window.visualViewport?.addEventListener('resize', fit);
  window.visualViewport?.addEventListener('scroll', fit);
  window.addEventListener('resize', fit);

  paint();

  return {
    state: () => ({
      touch,
      editing,
      editors: editors().length === EDITOR_IDS.length,
      canvas: gridCanvas() !== null,
      editButton: editButton.isConnected && editButton.parentElement?.getAttribute('data-u-comp') === 'formula-bar-actions',
      keyboardInset,
      compact,
    }),
    workbookLoaded() {
      if (TOUCH_DEFAULT_ZOOM === 1 || !coarse.matches) return;
      for (const sheet of workbook()?.getSheets() ?? []) {
        if (sheet.getZoom() === 1) sheet.zoom(TOUCH_DEFAULT_ZOOM);
      }
    },
  };
}
