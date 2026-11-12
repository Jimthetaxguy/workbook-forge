// Acceptance: Univer as a view, Forge (WASM in a Web Worker) as the only
// calculator and source of truth.
//
// Fixture: fixtures/univer-spike-chain.xlsx, A1 = 10 and five dependent
// formulas that already exist in the XLSX. Expected values are independent
// arithmetic, never engine output:
//   A1=10: B1=A1*2=20, C1=B1+5=25, D1=C1*3=75, E1=D1-B1=55, F1=SUM(A1:E1)=185
//   A1=20: 40, 45, 135, 95, 335
//   C1=B1+100 (A1=20): C1=140, D1=420, E1=380, F1=20+40+140+420+380=1000
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';

const ADDRESSES = ['A1', 'B1', 'C1', 'D1', 'E1', 'F1'] as const;
const INITIAL = [10, 20, 25, 75, 55, 185];
const AFTER_INPUT = [20, 40, 45, 135, 95, 335];
const AFTER_FORMULA = [20, 40, 140, 420, 380, 1000];
const FORMULAS = { B1: 'A1*2', C1: 'B1+5', D1: 'C1*3', E1: 'D1-B1', F1: 'SUM(A1:E1)' };

// Cell centres from the grid canvas's left edge (column width 88px, row 24px).
const COLS: Record<string, number> = { A: 90, B: 178, C: 266, D: 354, E: 442, F: 530, G: 618, H: 706, I: 794 };

async function boot(page: Page, mode: string) {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`/?mode=${mode}`);
  await page.waitForFunction(() => (window as any).__spike, null, { timeout: 30_000 });
  await page.evaluate(() => (window as any).__spike.then(() => undefined));
  if (mode !== 'engine-only' && mode !== 'no-formula-plugins') {
    // Univer creates its cell and formula-bar editors after first render.
    await page.waitForFunction(
      () => (window as any).__spike.then((s: any) => s.adapter.docUnitIds().length === 2),
      null,
      { timeout: 15_000 },
    );
  }
  if (mode !== 'engine-only' && mode !== 'no-formula-plugins') {
    await focusGrid(page);
  } else {
    await clickCell(page, 'H5');
    await page.waitForTimeout(400);
  }
  return errors;
}

// Row 1's centre sits 32px below the grid canvas top (20px column header plus
// half a 24px row).
const ROW1_CENTRE = 32;

// The grid canvas's page position. Measured rather than fixed because the app
// bar, the Developer strip and an open side panel all move or resize it.
async function gridOrigin(page: Page) {
  return page.evaluate(() => {
    const rects = [...document.querySelectorAll('canvas')]
      .map((canvas) => canvas.getBoundingClientRect())
      .filter((rect) => rect.width > 0 && rect.height > 0);
    // Before Univer renders (or in modes without a grid) fall back to the
    // default 1280x800 layout, where the grid canvas starts at y=76 (a 48px
    // app bar and Univer's 28px formula bar).
    if (rects.length === 0) return { x: 0, y: 76 };
    const grid = rects.reduce((a, b) => (a.width * a.height >= b.width * b.height ? a : b));
    return { x: grid.x, y: grid.y };
  });
}

async function clickCell(page: Page, address: string) {
  const origin = await gridOrigin(page);
  await page.mouse.click(
    origin.x + COLS[address[0]],
    origin.y + ROW1_CENTRE + (Number(address.slice(1)) - 1) * 24,
  );
}

// The first click focuses Univer's canvas; the editor is ready after it. The
// canvas is drawn a moment after the workbook and its editors exist, so click
// until the selection actually lands on H5.
async function focusGrid(page: Page) {
  const selected = () =>
    page.evaluate(() =>
      (window as any).__spike.then((s: any) => {
        try {
          return s.host.univerAPI.getActiveWorkbook().getActiveSheet().getSelection().getActiveRange().getA1Notation();
        } catch {
          return null;
        }
      }),
    );
  await expect
    .poll(
      async () => {
        await clickCell(page, 'H5');
        await page.waitForTimeout(100);
        return selected();
      },
      { timeout: 10_000 },
    )
    .toBe('H5');
  await page.waitForTimeout(300);
}

/** Type into a cell through Univer's real cell editor and commit with Enter. */
async function typeInto(page: Page, address: string, text: string) {
  await clickCell(page, address);
  await page.waitForTimeout(150);
  await page.keyboard.type(text, { delay: 15 });
  await page.keyboard.press('Enter');
  await settle(page);
}

async function settle(page: Page) {
  await page.waitForTimeout(250);
  await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.idle().then(() => undefined)));
}

async function view(page: Page) {
  return page.evaluate(async (addresses) => {
    const s = await (window as any).__spike;
    return {
      revision: s.adapter.revision as string,
      cells: Object.fromEntries(addresses.map((a: string) => [a, s.adapter.cell('Sheet1', a)])),
      divergence: s.adapter.divergence() as string[],
      audit: s.adapter.audit,
      notices: s.adapter.notices as string[],
    };
  }, [...ADDRESSES, 'H10']);
}

/** Forge's own state, read straight from the worker through the command API. */
async function forge(page: Page) {
  return page.evaluate(async () => {
    const s = await (window as any).__spike;
    const [snapshot, calc] = await Promise.all([s.client.snapshot(), s.client.calculate()]);
    return { snapshot: snapshot.value, calc: calc.value };
  });
}

function expectDisplayed(v: Awaited<ReturnType<typeof view>>, values: number[]) {
  ADDRESSES.forEach((address, i) => {
    expect(v.cells[address].displayed, `${address} displayed`).toBe(values[i]);
    expect(v.cells[address].raw, `${address} raw`).toBe(values[i]);
  });
  expect(v.divergence).toEqual([]);
}

function expectUniverNeverCalculated(v: Awaited<ReturnType<typeof view>>) {
  expect(v.audit.calcRequests, 'Univer formula calculation requests').toEqual([]);
  expect(v.audit.calcOutputs, 'Univer formula calculation results').toEqual([]);
  expect(v.audit.facadeEvents, 'univerAPI.getFormula() calculation events').toEqual([]);
  expect(v.audit.foreignWrites, 'cell writes not from the projection').toEqual([]);
}

/** Select a cell and read Univer's formula bar (re-selecting while it settles). */
async function formulaBarFor(page: Page, address: string) {
  await clickCell(page, address);
  await page.waitForTimeout(100);
  return page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.formulaBarText()));
}

for (const mode of ['not-execute', 'load-only']) {
  test.describe(`${mode}: Forge is the only calculator`, () => {
    test('switching workbooks clears the previous projection before recovery', async ({ page }) => {
      const errors = await boot(page, mode);
      await page.locator('#file').setInputFiles('tests/fixtures/sales-bang-boolean.xlsx');
      await expect.poll(() => page.evaluate(() =>
        (window as any).__spike.then((s: any) => s.adapter.cell('Sales!2026', 'A1').raw),
      )).toBe(7);

      // XFE10 is past Excel's last column, so Forge refuses the insertion
      // and the adapter recovers by reprojecting the new workbook only.
      const afterRejectedEdit = await page.evaluate(async () => {
        const s = await (window as any).__spike;
        await s.adapter.edit([{ sheet: 'Sales!2026', address: 'XFE10', value: 7 }], 'new workbook recovery');
        const snapshot = await s.client.snapshot();
        return {
          visibleOldCells: ['B1', 'C1', 'D1', 'E1', 'F1'].map((address) => s.adapter.cell('Sheet1', address).raw),
          newSheetValue: s.adapter.cell('Sales!2026', 'A1').raw,
          boolean: s.adapter.cell('Sales!2026', 'B1'),
          forgeCells: Object.keys(snapshot.value.sheets[0].cells),
          divergence: s.adapter.divergence(),
          notice: s.adapter.notices.at(-1),
        };
      });
      expect(afterRejectedEdit.forgeCells).toEqual(['A1', 'B1']);
      expect(afterRejectedEdit.newSheetValue).toBe(7);
      expect(afterRejectedEdit.boolean.raw).toBe(true);
      expect(afterRejectedEdit.visibleOldCells).toEqual([null, null, null, null, null]);
      expect(afterRejectedEdit.divergence).toEqual([]);
      expect(afterRejectedEdit.notice).toContain('out_of_bounds');
      expect(errors).toEqual([]);
    });

    test('an invalid replacement file leaves the current workbook visible', async ({ page }) => {
      const errors = await boot(page, mode);
      const before = await view(page);
      await page.locator('#file').setInputFiles({
        name: 'invalid.xlsx',
        mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        buffer: Buffer.from('not an xlsx package'),
      });
      await expect(page.locator('#notices')).toContainText('Could not open invalid.xlsx');
      await expect(page.locator('#notices')).toContainText('open_failed');
      const after = await view(page);
      expect(after.revision).toBe(before.revision);
      expectDisplayed(after, INITIAL);
      expect(after.divergence).toEqual([]);
      expect(errors).toEqual([]);
    });

    test('value edit, formula edit, undo/redo, export round trip', async ({ page }) => {
      const errors = await boot(page, mode);

      // Load: Forge values projected next to the XLSX formulas.
      let v = await view(page);
      expect(v.revision).toBe('0');
      expectDisplayed(v, INITIAL);
      for (const [address, formula] of Object.entries(FORMULAS)) expect(v.cells[address].formula).toBe(`=${formula}`);
      await expect.poll(() => formulaBarFor(page, 'C1')).toBe('=B1+5');

      // Value edit through the cell editor.
      await typeInto(page, 'A1', '20');
      v = await view(page);
      expect(v.revision).toBe('1');
      expectDisplayed(v, AFTER_INPUT);
      expect(v.audit.routed).toContain('sheet.command.set-range-values');

      // Formula edit through the cell editor.
      await typeInto(page, 'C1', '=B1+100');
      v = await view(page);
      expect(v.revision).toBe('2');
      expectDisplayed(v, AFTER_FORMULA);
      expect(v.cells.C1.formula).toBe('=B1+100');
      await expect.poll(() => formulaBarFor(page, 'C1')).toBe('=B1+100');
      let f = await forge(page);
      expect(f.snapshot.revision).toBe('2');
      expect(f.snapshot.sheets[0].cells.C1.formula).toBe('B1+100');
      expect(f.calc.values['Sheet1!F1']).toBe(1000);

      // Undo: Univer's undo command is routed to Forge as an undo intent
      // (revision 2 -> 3).
      await page.keyboard.press(process.platform === 'darwin' ? 'Meta+z' : 'Control+z');
      await settle(page);
      v = await view(page);
      expect(v.audit.routed).toContain('univer.command.undo');
      expect(v.revision).toBe('3');
      expectDisplayed(v, AFTER_INPUT);
      expect(v.cells.C1.formula).toBe('=B1+5');
      f = await forge(page);
      expect(f.snapshot.revision).toBe('3');
      expect(f.snapshot.sheets[0].cells.C1.formula).toBe('B1+5');
      expect(f.calc.values['Sheet1!F1']).toBe(335);

      // Redo through the adapter's Forge history (3 -> 4).
      await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.redo().then(() => undefined)));
      v = await view(page);
      expect(v.revision).toBe('4');
      expectDisplayed(v, AFTER_FORMULA);

      // Edits, undo and redo went through command envelopes, not applyEdits.
      const commands: string[] = await page.evaluate(() =>
        (window as any).__spike.then((s: any) => s.client.log.map((entry: any) => entry.command)),
      );
      expect(commands.filter((c) => c === 'applyAndCalculate').length).toBeGreaterThanOrEqual(4);
      expect(commands).not.toContain('applyEdits');

      // A stale revision is refused by Forge with structured revision data.
      const stale = await page.evaluate(() =>
        (window as any).__spike.then((s: any) => s.client.applyEdits([{ sheet: 'Sheet1', address: 'A1', value: 1 }], '3')),
      );
      expect(stale.ok).toBe(false);
      expect(stale.error).toMatchObject({ code: 'revision_conflict', expected_revision: '3', current_revision: '4' });

      // Export from Forge, reopen in a fresh worker, check values and formulas.
      const reopened = await page.evaluate(async () => {
        const s = await (window as any).__spike;
        const bytes: Uint8Array = await s.adapter.exportXlsx();
        const { client, worker } = s.startForgeWorker();
        const opened = await client.open(bytes);
        const [snapshot, calc] = await Promise.all([client.snapshot(), client.calculate()]);
        worker.terminate();
        return { size: bytes.length, opened, snapshot: snapshot.value, values: calc.value.values };
      });
      expect(reopened.size).toBeGreaterThan(0);
      expect(reopened.opened).toEqual({ ok: true, value: { revision: '0', sheets: ['Sheet1'] } });
      const cells = reopened.snapshot.sheets[0].cells;
      expect(cells.A1.value).toBe(20);
      expect(cells.C1.formula).toBe('B1+100');
      for (const [address, formula] of Object.entries({ ...FORMULAS, C1: 'B1+100' })) expect(cells[address].formula).toBe(formula);
      ADDRESSES.forEach((address, i) => expect(reopened.values[`Sheet1!${address}`]).toBe(AFTER_FORMULA[i]));

      v = await view(page);
      expectUniverNeverCalculated(v);
      expect(errors).toEqual([]);
    });

    test('structural and out-of-scope mutations are blocked with a visible notice', async ({ page }) => {
      await boot(page, mode);
      const before = await view(page);
      // The notice says what is unavailable in plain words; the command id
      // goes to the audit (and the Developer strip), not to the reader.
      for (const [id, sentence] of [
        ['sheet.command.insert-row-before', 'Inserting rows and columns is not available yet.'],
        ['sheet.command.insert-col-before', 'Inserting rows and columns is not available yet.'],
        ['sheet.command.remove-row-confirm', 'Deleting rows and columns is not available yet.'],
        ['sheet.command.insert-sheet', 'Adding, removing and renaming sheets is not available yet.'],
        // Fill, paste and clear reach Forge through the fill handle, Ctrl+D,
        // Ctrl+R, Ctrl+V and Delete (see "everyday editing" below). Every
        // other way in is still refused.
        ['sheet.command.auto-fill', 'Fill options are not available yet.'],
        ['univer.command.paste', 'Paste with Ctrl+V (Cmd+V on a Mac). Pasting from the menu and Paste Special are not available yet.'],
        ['univer.command.cut', 'Cut is not available yet. Copy and paste, then clear the original with Delete.'],
        ['sheet.command.clear-selection-format', 'Clearing formats is not available yet. Delete clears the contents.'],
        ['sheet.command.clear-selection-all', 'Clearing formats is not available yet. Delete clears the contents.'],
        ['sheet.command.delete-range-move-left', 'That way of clearing cells is not available yet. Select the cells and press Delete.'],
      ]) {
        const result = await page.evaluate(
          (commandId) => (window as any).__spike.then((s: any) => s.host.univerAPI.executeCommand(commandId)),
          id,
        );
        expect(result, id).toBe(false);
        await expect(page.locator('#notices'), id).toHaveText(sentence);
        await expect(page.locator('#notice'), id).toBeVisible();
        expect(await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.audit.blocked.at(-1))), id).toBe(id);
      }
      const after = await view(page);
      expect(after.audit.blocked).toEqual(expect.arrayContaining(['sheet.command.insert-row-before', 'sheet.command.auto-fill', 'univer.command.paste']));
      expect(after.revision).toBe(before.revision);
      const shape = await page.evaluate(() =>
        (window as any).__spike.then((s: any) => {
          const wb = s.host.univerAPI.getActiveWorkbook();
          return { sheets: wb.getSheets().length, rows: wb.getActiveSheet().getMaxRows(), cols: wb.getActiveSheet().getMaxColumns() };
        }),
      );
      // The view shows at least 200 rows and 26 columns of an opened workbook.
      expect(shape).toEqual({ sheets: 1, rows: 200, cols: 26 });
      expectDisplayed(after, INITIAL);
      expectUniverNeverCalculated(after);
    });

    // Usable-demo acceptance step 3: enter data into an initially absent cell
    // of an imported workbook. univer-spike-chain.xlsx holds only row 1, so
    // H1 and A2 are absent. Independent arithmetic: H1 = SUM(A1:A3) over
    // A1 = 10 and two blanks is 10; typing A2 = 5 makes it 15.
    test('typing into an absent cell inside a SUM range repaints, undoes to absence, exports and reopens', async ({ page }) => {
      const errors = await boot(page, mode);
      const read = () =>
        page.evaluate(async () => {
          const s = await (window as any).__spike;
          const snapshot = await s.client.snapshot();
          return {
            revision: s.adapter.revision as string,
            h1: s.adapter.cell('Sheet1', 'H1'),
            a2: s.adapter.cell('Sheet1', 'A2'),
            forgeCells: Object.keys(snapshot.value.sheets[0].cells),
            divergence: s.adapter.divergence() as string[],
            notices: s.adapter.notices as string[],
          };
        });
      const reopen = () =>
        page.evaluate(async () => {
          const s = await (window as any).__spike;
          const bytes: Uint8Array = await s.adapter.exportXlsx();
          const { client, worker } = s.startForgeWorker();
          const opened = await client.open(bytes);
          const [snapshot, calc] = await Promise.all([client.snapshot(), client.calculate()]);
          worker.terminate();
          return { opened, cells: snapshot.value.sheets[0].cells, values: calc.value.values };
        });

      // A formula typed into absent H1 over a range that is mostly blank.
      await typeInto(page, 'H1', '=SUM(A1:A3)');
      let v = await read();
      expect(v.notices.filter((n) => n.startsWith('Change not applied'))).toEqual([]);
      expect(v.revision).toBe('1');
      expect(v.h1.displayed).toBe(10);
      expect(v.h1.formula).toBe('=SUM(A1:A3)');

      // Typing into absent A2, inside the SUM range, repaints the total.
      await typeInto(page, 'A2', '5');
      v = await read();
      expect(v.revision).toBe('2');
      expect(v.a2.displayed).toBe(5);
      expect(v.h1.displayed).toBe(15);
      expect(v.forgeCells).toContain('A2');
      expect(v.divergence).toEqual([]);

      // Undo (Univer's shortcut, routed to Forge) makes A2 absent again.
      await page.keyboard.press(process.platform === 'darwin' ? 'Meta+z' : 'Control+z');
      await settle(page);
      v = await read();
      expect(v.revision).toBe('3');
      expect(v.a2.raw).toBeNull();
      expect(v.h1.displayed).toBe(10);
      expect(v.forgeCells).not.toContain('A2');
      expect(v.divergence).toEqual([]);

      // Export after the undo and reopen: H1 kept, A2 still absent.
      let reopened = await reopen();
      expect(reopened.opened.ok).toBe(true);
      expect(reopened.cells.H1.formula).toBe('SUM(A1:A3)');
      expect('A2' in reopened.cells).toBe(false);
      expect(reopened.values['Sheet1!H1']).toBe(10);

      // Redo, export again: the inserted value and the total survive.
      await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.redo().then(() => undefined)));
      v = await read();
      expect(v.revision).toBe('4');
      expect(v.a2.displayed).toBe(5);
      expect(v.h1.displayed).toBe(15);
      reopened = await reopen();
      expect(reopened.cells.A2.value).toBe(5);
      expect(reopened.values['Sheet1!H1']).toBe(15);
      expect(reopened.values['Sheet1!F1']).toBe(185);

      expectUniverNeverCalculated(await view(page));
      expect(errors).toEqual([]);
    });
  });
}

test('blank workbook authors new cells, evaluates formulas and exports locally', async ({ page }) => {
  const errors: string[] = [];
  const requests: { url: string; method: string; postData: string | null }[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (request) => {
    requests.push({ url: request.url(), method: request.method(), postData: request.postData() });
  });
  await boot(page, 'not-execute');
  await page.locator('#new').click();
  await expect.poll(() => page.evaluate(() =>
    (window as any).__spike.then(async (s: any) => {
      await s.adapter.idle();
      const snapshot = await s.client.snapshot();
      return { sheets: snapshot.value.sheets.map((sheet: any) => sheet.name), cells: Object.keys(snapshot.value.sheets[0].cells) };
    }),
  )).toEqual({ sheets: ['Sheet1'], cells: [] });
  await page.waitForFunction(
    () => (window as any).__spike.then((s: any) => s.adapter.docUnitIds().length === 2),
    null,
    { timeout: 15_000 },
  );
  await page.waitForTimeout(400);
  await typeInto(page, 'A1', '2');
  await typeInto(page, 'A2', '3');
  // Type the range rather than drag-selecting it: a mouse drag lands on
  // pixel coordinates and intermittently missed A1:A2, which dropped the
  // A3 edit and left the revision short of 17.
  await typeInto(page, 'A3', '=SUM(A1:A2)');
  await typeInto(page, 'A4', '=A1+A2');
  await typeInto(page, 'B1', '=IF(A3=5,"pass","fail")');
  await typeInto(page, 'C1', 'apple');
  await typeInto(page, 'D1', '10');
  await typeInto(page, 'C2', 'pear');
  await typeInto(page, 'D2', '20');
  await typeInto(page, 'C3', 'pear');
  await typeInto(page, 'D3', '30');
  await typeInto(page, 'D4', '40');
  await typeInto(page, 'E1', '=XLOOKUP("pear",C1:C2,D1:D2)');
  await typeInto(page, 'F1', '=INDEX(D1:D2,MATCH("pear",C1:C2,0))');
  await typeInto(page, 'G1', '=XLOOKUP("plum",C1:C3,D1:D3,"not found")');
  await typeInto(page, 'H1', '=XLOOKUP("pear",C1:C3,D1:D3,,,-1)');
  await typeInto(page, 'I1', '=XLOOKUP(,C1:C4,D1:D4,"blank key missing")');

  const state = await page.evaluate(async () => {
    const s = await (window as any).__spike;
    const [snapshot, calculation] = await Promise.all([s.client.snapshot(), s.client.calculate()]);
    return { snapshot: snapshot.value, calculation: calculation.value, divergence: s.adapter.divergence() };
  });
  expect(state.snapshot.revision).toBe('17');
  expect(state.snapshot.sheets[0].cells.A1.value).toBe(2);
  expect(state.snapshot.sheets[0].cells.A3.formula).toBe('SUM(A1:A2)');
  expect(state.snapshot.sheets[0].cells.A4.formula).toBe('A1+A2');
  expect(state.snapshot.sheets[0].cells.B1.formula).toBe('IF(A3=5,"pass","fail")');
  expect(state.snapshot.sheets[0].cells.E1.formula).toBe('XLOOKUP("pear",C1:C2,D1:D2)');
  expect(state.snapshot.sheets[0].cells.F1.formula).toBe('INDEX(D1:D2,MATCH("pear",C1:C2,0))');
  expect(state.snapshot.sheets[0].cells.G1.formula).toBe('XLOOKUP("plum",C1:C3,D1:D3,"not found")');
  expect(state.snapshot.sheets[0].cells.H1.formula).toBe('XLOOKUP("pear",C1:C3,D1:D3,,,-1)');
  expect(state.snapshot.sheets[0].cells.I1.formula).toBe('XLOOKUP(,C1:C4,D1:D4,"blank key missing")');
  expect(state.calculation.values['Sheet1!A3']).toBe(5);
  expect(state.calculation.values['Sheet1!A4']).toBe(5);
  expect(state.calculation.values['Sheet1!B1']).toBe('pass');
  expect(state.calculation.values['Sheet1!E1']).toBe(20);
  expect(state.calculation.values['Sheet1!F1']).toBe(20);
  expect(state.calculation.values['Sheet1!G1']).toBe('not found');
  expect(state.calculation.values['Sheet1!H1']).toBe(30);
  expect(state.calculation.values['Sheet1!I1']).toBe(40);
  expect(state.divergence).toEqual([]);

  const roundTrip = await page.evaluate(async () => {
    const s = await (window as any).__spike;
    const bytes: Uint8Array = await s.adapter.exportXlsx();
    const reopened = s.startForgeWorker();
    const opened = await reopened.client.open(bytes);
    const [snapshot, calculation] = await Promise.all([reopened.client.snapshot(), reopened.client.calculate()]);
    reopened.worker.terminate();
    return { opened, snapshot: snapshot.value, calculation: calculation.value };
  });
  expect(roundTrip.opened).toEqual({ ok: true, value: { revision: '0', sheets: ['Sheet1'] } });
  expect(roundTrip.snapshot.sheets[0].cells.A3.formula).toBe('SUM(A1:A2)');
  expect(roundTrip.snapshot.sheets[0].cells.A4.formula).toBe('A1+A2');
  expect(roundTrip.snapshot.sheets[0].cells.E1.formula).toBe('XLOOKUP("pear",C1:C2,D1:D2)');
  expect(roundTrip.snapshot.sheets[0].cells.F1.formula).toBe('INDEX(D1:D2,MATCH("pear",C1:C2,0))');
  expect(roundTrip.snapshot.sheets[0].cells.G1.formula).toBe('XLOOKUP("plum",C1:C3,D1:D3,"not found")');
  expect(roundTrip.snapshot.sheets[0].cells.H1.formula).toBe('XLOOKUP("pear",C1:C3,D1:D3,,,-1)');
  expect(roundTrip.snapshot.sheets[0].cells.I1.formula).toBe('XLOOKUP(,C1:C4,D1:D4,"blank key missing")');
  expect(roundTrip.calculation.values['Sheet1!A3']).toBe(5);
  expect(roundTrip.calculation.values['Sheet1!A4']).toBe(5);
  expect(roundTrip.calculation.values['Sheet1!B1']).toBe('pass');
  expect(roundTrip.calculation.values['Sheet1!E1']).toBe(20);
  expect(roundTrip.calculation.values['Sheet1!F1']).toBe(20);
  expect(roundTrip.calculation.values['Sheet1!G1']).toBe('not found');
  expect(roundTrip.calculation.values['Sheet1!H1']).toBe(30);
  expect(roundTrip.calculation.values['Sheet1!I1']).toBe(40);
  expect(requests.every((request) => request.method === 'GET' && request.postData === null)).toBe(true);
  // Every request is for the page's own files: no fonts, scripts or calls to another site.
  const origin = new URL(page.url()).origin;
  expect(requests.map((request) => request.url).filter((url) => !url.startsWith(`${origin}/`) && !/^(data|blob):/.test(url))).toEqual([]);
  expect(errors).toEqual([]);
});

test('control: stock formula plugins calculate, and the spy sees it', async ({ page }) => {
  await boot(page, 'univer-default');
  await page.waitForTimeout(1000);
  const v = await view(page);
  // Univer's own engine ran on load (stock WHEN_EMPTY mode), which the audit
  // must catch. Its results happen to equal Forge's for this fixture, so a
  // value diff alone would not have noticed: the mutation/facade spy is what
  // makes silent recalculation visible.
  expect(v.audit.calcRequests.length).toBeGreaterThan(0);
  expect(v.audit.calcOutputs.length + v.audit.facadeEvents.length).toBeGreaterThan(0);
});

for (const mode of ['engine-only', 'no-formula-plugins']) {
  test(`${mode}: no formula plugins means no cell editor or formula bar`, async ({ page }) => {
    await boot(page, mode);
    const docs = await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.docUnitIds()));
    expect(docs).toEqual([]);
    await typeInto(page, 'A1', '20');
    const v = await view(page);
    // The keystrokes never reach an editor, so nothing is routed or changed.
    expect(v.audit.routed).toEqual([]);
    expect(v.revision).toBe('0');
    expectDisplayed(v, INITIAL);
    expectUniverNeverCalculated(v);
  });
}

// Event log v1 and log-based undo (realtime design section 4, PR 2).
// Fixture fixtures/realtime-chain.xlsx: A1 = 10, B1 = 0 (literal placeholder),
// C1 = B1+5, D1 = C1*3, E1 = D1-B1, F1 = SUM(A1:E1). Independent arithmetic:
//   start:             10, 0, 5, 15, 15, 45
//   B1 = A1*2:         10, 20, 25, 75, 55, 185
//   A1 = 20:           20, 40, 45, 135, 95, 335
// B1 starts as a literal so typing =A1*2 is a real change: Univer's editor
// skips the set-range-values command when the committed cell equals the raw one.
const REALTIME_FIXTURE = '../../fixtures/realtime-chain.xlsx';
const RT_START = [10, 0, 5, 15, 15, 45];
const RT_FORMULA = [10, 20, 25, 75, 55, 185];
const RT_INPUT = [20, 40, 45, 135, 95, 335];
// 100 ms unless SPIKE_REPAINT_GATE_MS names another positive budget, for a
// host known to be slower (the default is what the acceptance results claim).
const REPAINT_GATE_MS = (() => {
  const raw = process.env.SPIKE_REPAINT_GATE_MS;
  if (raw === undefined || raw === '') return 100;
  const ms = Number(raw);
  if (!Number.isFinite(ms) || ms <= 0) throw new Error(`SPIKE_REPAINT_GATE_MS must be a positive number; received ${raw}`);
  return ms;
})();
// Repaint latency is gated on the median of REPAINT_SAMPLES committed edits,
// after REPAINT_WARMUPS unmeasured ones. One sample is at the mercy of a GC
// pause or a busy CI host; a median of warmed samples still fails when the
// typical commit-to-paint path is slower than the gate, which is the thing
// the gate exists to catch. No single sample may exceed REPAINT_CEILING_MS.
const REPAINT_WARMUPS = 1;
const REPAINT_SAMPLES = 5;
const REPAINT_CEILING_MS = 1000;
// __b1At is written inside requestAnimationFrame, after settle()'s 250 ms pad
// and adapter.idle(). Poll past the per-sample ceiling so a slow paint is
// recorded and that ceiling can reject it. This wait is not a gate threshold.
const REPAINT_SEEN_TIMEOUT_MS = REPAINT_CEILING_MS + 4_000;

async function waitForB1Paint(page: Page, shown: string) {
  await expect.poll(
    () => page.evaluate(() => (window as any).__b1At as number | null),
    { timeout: REPAINT_SEEN_TIMEOUT_MS, message: `B1 never showed ${shown}` },
  ).not.toBeNull();
}

test('event log: typed edits are logged human intents with exact deltas, Ctrl+Z is an undo intent, and the log replays', async ({ page }) => {
  const errors = await boot(page, 'not-execute');
  await page.locator('#file').setInputFiles(REALTIME_FIXTURE);
  await expect.poll(() => page.evaluate(() =>
    (window as any).__spike.then((s: any) => [s.adapter.cell('Sheet1', 'B1').raw, s.adapter.cell('Sheet1', 'C1').raw]),
  )).toEqual([0, 5]);
  let v = await view(page);
  expect(v.revision).toBe('0');
  expectDisplayed(v, RT_START);
  // Stream log events into the page, as a log panel would.
  await page.evaluate(() => (window as any).__spike.then(async (s: any) => {
    (window as any).__events = [];
    s.client.onEvent((event: unknown) => (window as any).__events.push(event));
    await s.client.subscribe({ events: true });
  }));
  await clickCell(page, 'H5');
  await page.waitForTimeout(400);

  // 1. =A1*2 into B1 through the real cell editor.
  await typeInto(page, 'B1', '=A1*2');
  v = await view(page);
  expect(v.revision).toBe('1');
  expectDisplayed(v, RT_FORMULA);
  expect(v.cells.B1.formula).toBe('=A1*2');

  // 2. 20 into A1; B1 must repaint 40 within the gate of the commit. A frame
  // watcher in the page records when Univer first shows B1 = 40.
  await clickCell(page, 'A1');
  await page.waitForTimeout(150);
  await page.keyboard.type('20', { delay: 15 });
  await page.evaluate(() => (window as any).__spike.then((s: any) => {
    const w = window as any;
    w.__b1At = null;
    const tick = () => {
      if (s.adapter.cell('Sheet1', 'B1').displayed === 40) w.__b1At = performance.now();
      else requestAnimationFrame(tick);
    };
    tick();
  }));
  await page.keyboard.press('Enter');
  await settle(page);
  await waitForB1Paint(page, '40');
  const timing = await page.evaluate(() => (window as any).__spike.then((s: any) => ({
    commitAt: s.adapter.lastCommitAt as number,
    paintAt: s.adapter.lastPaintAt as number,
    seenAt: (window as any).__b1At as number | null,
  })));
  expect(timing.seenAt, 'B1 never showed 40').not.toBeNull();
  const repaintMs = (timing.seenAt as number) - timing.commitAt;
  test.info().annotations.push({ type: 'b1-repaint-ms', description: repaintMs.toFixed(1) });
  test.info().annotations.push({ type: 'forge-response-paint-ms', description: (timing.paintAt - timing.commitAt).toFixed(1) });
  console.log(`event-log: B1 repainted 40 ${repaintMs.toFixed(1)} ms after the A1 commit (Forge response painted at ${(timing.paintAt - timing.commitAt).toFixed(1)} ms)`);
  // The latency gate itself lives in the dedicated repaint test below, which
  // samples several warmed commits; this single first commit after load is
  // recorded for the log but carries cold-start cost on a loaded machine.
  expect(repaintMs).toBeGreaterThanOrEqual(0);
  v = await view(page);
  expect(v.revision).toBe('2');
  expectDisplayed(v, RT_INPUT);

  // 3. Exactly two human intents at revisions 1 and 2, and their calc deltas.
  const readLog = () => page.evaluate(() => (window as any).__spike.then((s: any) => s.client.readLog(0).then((r: any) => r.value.events)));
  let log = await readLog();
  expect(log[0]).toMatchObject({ kind: 'session', op: 'opened', seq: 0, revision: '0', baseline: { kind: 'xlsx', name: 'realtime-chain.xlsx' } });
  let intents = log.filter((e: any) => e.kind === 'intent');
  expect(intents).toHaveLength(2);
  expect(intents.map((e: any) => [e.actor.type, e.status, e.revision])).toEqual([['human', 'applied', '1'], ['human', 'applied', '2']]);
  expect(intents[0].command).toMatchObject({ name: 'edits', source: 'editor', univer: 'sheet.command.set-range-values' });
  expect(intents[0].inputs).toEqual([{ sheet: 'Sheet1', address: 'B1', before: { value: 0 }, after: { formula: 'A1*2' } }]);
  expect(intents[1].inputs).toEqual([{ sheet: 'Sheet1', address: 'A1', before: { value: 10 }, after: { value: 20 } }]);
  const calcs = log.filter((e: any) => e.kind === 'calc');
  expect(calcs.map((e: any) => e.caused_by)).toEqual([[intents[0].id], [intents[1].id]]);
  expect(calcs.map((e: any) => e.revision)).toEqual(['1', '2']);
  expect(calcs.map((e: any) => e.deltas)).toEqual([
    [['Sheet1!B1', 0, 20], ['Sheet1!C1', 5, 25], ['Sheet1!D1', 15, 75], ['Sheet1!E1', 15, 55], ['Sheet1!F1', 45, 185]],
    [['Sheet1!B1', 20, 40], ['Sheet1!C1', 25, 45], ['Sheet1!D1', 75, 135], ['Sheet1!E1', 55, 95], ['Sheet1!F1', 185, 335]],
  ]);

  // 4. Ctrl+Z: a third intent, undo of the A1 event, revision 3, B1 back to 20.
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+z' : 'Control+z');
  await settle(page);
  v = await view(page);
  expect(v.audit.routed).toContain('univer.command.undo');
  expect(v.revision).toBe('3');
  expect(v.cells.B1.displayed).toBe(20);
  expectDisplayed(v, RT_FORMULA);
  log = await readLog();
  intents = log.filter((e: any) => e.kind === 'intent');
  expect(intents).toHaveLength(3);
  expect(intents[2]).toMatchObject({
    actor: { type: 'human' },
    status: 'applied',
    revision: '3',
    command: { name: 'undo', target: intents[1].id },
    inputs: [{ sheet: 'Sheet1', address: 'A1', before: { value: 20 }, after: { value: 10 } }],
  });
  expect(log.at(-1)).toMatchObject({ kind: 'calc', caused_by: [intents[2].id], revision: '3' });
  // subscribe {events:true} streamed the same events, in order.
  const streamed = await page.evaluate(() => (window as any).__events);
  expect(streamed).toEqual(log);

  // 5. Export with the log and replay it in a fresh worker onto the fixture.
  const fixtureBytes = [...readFileSync(REALTIME_FIXTURE)];
  const replayed = await page.evaluate(async (bytes) => {
    const s = await (window as any).__spike;
    const exported = await s.client.export({ includeLog: true });
    const { client, worker } = s.startForgeWorker();
    const result = await client.replay(new Uint8Array(bytes), exported.value.log);
    worker.terminate();
    return { logName: exported.value.logName, last: JSON.parse(exported.value.log.trim().split('\n').at(-1)), result };
  }, fixtureBytes);
  expect(replayed.logName).toBe('realtime-chain.forge-log.ndjson');
  expect(replayed.last).toMatchObject({ kind: 'session', op: 'exported', revision: '3' });
  expect(replayed.result.ok, JSON.stringify(replayed.result.error)).toBe(true);
  expect(replayed.result.value.revision).toBe('3');
  expect(replayed.result.value.calcEvents.map((c: any) => c.deltas)).toEqual(log.filter((e: any) => e.kind === 'calc').map((e: any) => e.deltas));
  ADDRESSES.forEach((address, i) => expect(replayed.result.value.values[`Sheet1!${address}`]).toBe(RT_FORMULA[i]));

  expectUniverNeverCalculated(v);
  expect(errors).toEqual([]);
});

test('repaint latency: a typed input commit repaints its dependent within the gate (median of warmed samples)', async ({ page }) => {
  const errors = await boot(page, 'not-execute');
  await page.locator('#file').setInputFiles(REALTIME_FIXTURE);
  await expect.poll(() => page.evaluate(() =>
    (window as any).__spike.then((s: any) => s.adapter.cell('Sheet1', 'B1').raw),
  )).toBe(0);
  await clickCell(page, 'H5');
  await page.waitForTimeout(400);
  await typeInto(page, 'B1', '=A1*2');
  expect((await view(page)).cells.B1.formula).toBe('=A1*2');

  const samples: number[] = [];
  for (let i = 0; i < REPAINT_WARMUPS + REPAINT_SAMPLES; i++) {
    const input = 21 + i;
    await clickCell(page, 'A1');
    await page.waitForTimeout(150);
    await page.keyboard.type(String(input), { delay: 15 });
    await page.evaluate((want) => (window as any).__spike.then((s: any) => {
      const w = window as any;
      w.__b1At = null;
      const tick = () => {
        if (s.adapter.cell('Sheet1', 'B1').displayed === want) w.__b1At = performance.now();
        else requestAnimationFrame(tick);
      };
      tick();
    }), input * 2);
    await page.keyboard.press('Enter');
    await settle(page);
    await waitForB1Paint(page, String(input * 2));
    const timing = await page.evaluate(() => (window as any).__spike.then((s: any) => ({
      commitAt: s.adapter.lastCommitAt as number,
      seenAt: (window as any).__b1At as number | null,
    })));
    expect(timing.seenAt, `B1 never showed ${input * 2}`).not.toBeNull();
    const ms = (timing.seenAt as number) - timing.commitAt;
    expect(ms).toBeGreaterThanOrEqual(0);
    if (i >= REPAINT_WARMUPS) samples.push(ms);
  }
  const sorted = [...samples].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  test.info().annotations.push({ type: 'b1-repaint-ms-samples', description: samples.map((m) => m.toFixed(1)).join(',') });
  test.info().annotations.push({ type: 'b1-repaint-ms-median', description: median.toFixed(1) });
  console.log(`repaint: B1 samples ${samples.map((m) => m.toFixed(1)).join(', ')} ms, median ${median.toFixed(1)} ms (gate ${REPAINT_GATE_MS})`);
  expect(median).toBeLessThan(REPAINT_GATE_MS);
  expect(sorted.at(-1) as number).toBeLessThan(REPAINT_CEILING_MS);
  expect((await view(page)).cells.B1.displayed).toBe((21 + REPAINT_WARMUPS + REPAINT_SAMPLES - 1) * 2);
});

test('text columns: a cell typed where none existed inherits its Text (@) row or column format', async ({ page }) => {
  const errors = await boot(page, 'not-execute');
  await page.locator('#file').setInputFiles('../../fixtures/corpus/hb_text_columns.xlsx');
  await expect.poll(() => page.evaluate(() =>
    (window as any).__spike.then((s: any) => s.adapter.cell('Codes', 'A2').raw),
  )).toBe('00017');
  const layout = await page.evaluate(() => (window as any).__spike.then(async (s: any) =>
    (await s.client.snapshot()).value.sheets[0].textFormat));
  expect(layout).toEqual({ columns: [[1, 1]], rows: [5], generalRows: [6] });
  // Column A is Text: new cells there keep what was typed as text.
  await typeInto(page, 'A4', '00123');
  // Row 5 is Text across every column; row 6's own number format wins over column A.
  await typeInto(page, 'E5', '42');
  await typeInto(page, 'A6', '8');
  // Column B has no style; A3 existed with its own General format.
  await typeInto(page, 'B4', '5');
  await typeInto(page, 'A3', '7');
  const cells = await page.evaluate(() => (window as any).__spike.then(async (s: any) =>
    (await s.client.snapshot()).value.sheets[0].cells));
  expect(cells.A4).toMatchObject({ value: '00123' });
  expect(cells.E5).toMatchObject({ value: '42' });
  expect(cells.A6).toMatchObject({ value: 8 });
  expect(cells.B4).toMatchObject({ value: 5 });
  expect(cells.A3).toMatchObject({ value: 7 });
  const notices = await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.notices as string[]));
  expect(notices.filter((n: string) => n.startsWith('Change not applied'))).toEqual([]);
  expect(errors).toEqual([]);
});

// The default URL is the user view: plain labels, no engine names, no raw
// revision, no mode selector. `?dev=1` (or any non-default `?mode=`) adds the
// Developer panel with those controls.
test('toolbar: the default view hides developer controls and ?dev=1 shows them', async ({ page }) => {
  const userView = async () => {
    const bar = (await page.locator('#bar').innerText()) + '\n' + (await page.locator('#privacy').innerText());
    return {
      bar,
      modeVisible: await page.locator('#mode').isVisible(),
      developerVisible: await page.locator('#developer').isVisible(),
      issuesVisible: (await page.locator('#formula-issues').isVisible()) || (await page.locator('#issues-toggle').isVisible()),
      xlookupNoteVisible: await page.locator('#formula-support').isVisible(),
      title: await page.title(),
    };
  };
  await page.goto('/');
  await page.evaluate(() => (window as any).__spike.then(() => undefined));
  await expect(page.locator('#status')).toHaveText('univer-spike-chain.xlsx · All changes calculated · Version 0');
  const plain = await userView();
  expect(plain.modeVisible, 'mode selector').toBe(false);
  expect(plain.developerVisible, 'Developer panel').toBe(false);
  expect(plain.xlookupNoteVisible, 'XLOOKUP subset note').toBe(false);
  expect(plain.issuesVisible, 'Formula issues with no issues').toBe(false);
  expect(plain.bar).not.toContain('(Forge)');
  expect(plain.bar).not.toMatch(/\br\d+\b/);
  expect(plain.bar).not.toContain('not-execute');
  expect(plain.title).toBe('univer-spike-chain.xlsx · Workbook Forge');
  for (const label of ['Workbook Forge', 'New workbook', 'Download .xlsx', 'Undo', 'Redo', 'Your workbook stays on this device. Use Download to save a copy.']) {
    expect(plain.bar).toContain(label);
  }
  expect(await page.evaluate(() => (window as any).__spike.then((s: any) => s.mode))).toBe('not-execute');

  // ?mode=not-execute is the same user view.
  await page.goto('/?mode=not-execute');
  await page.evaluate(() => (window as any).__spike.then(() => undefined));
  expect(await page.locator('#developer').isVisible()).toBe(false);

  for (const url of ['/?dev=1', '/?mode=load-only']) {
    await page.goto(url);
    await page.evaluate(() => (window as any).__spike.then(() => undefined));
    await expect(page.locator('#developer')).toBeVisible();
    expect(await page.locator('#mode').isVisible(), `${url}: mode selector before opening`).toBe(false);
    await page.locator('#developer > summary').click();
    await expect(page.locator('#mode')).toBeVisible();
    const expectedMode = url.includes('load-only') ? 'load-only' : 'not-execute';
    await expect(page.locator('#mode')).toHaveValue(expectedMode);
    await expect(page.locator('#dev-revision')).toHaveText(`Forge r0 · mode ${expectedMode}`);
    await expect(page.locator('#dev-source')).toContainText('univer-spike-chain.xlsx');
    await expect(page.locator('#formula-support')).toContainText('XLOOKUP subset');
    // The user-facing labels stay plain in dev mode too.
    expect(await page.locator('#bar').innerText()).not.toContain('(Forge)');
  }
});

// Formula issues lists Forge's calculation diagnostics, e.g. an XLOOKUP mode
// outside the supported subset or a function Forge does not implement. The
// app bar button shows only while there are some; its panel lists each one,
// and clicking one selects the cell.
test('formula issues: refused formulas are counted and listed, clicking one selects its cell, and the button hides when fixed', async ({ page }) => {
  await boot(page, 'not-execute');
  await page.locator('#new').click();
  await page.waitForFunction(
    () => (window as any).__spike.then((s: any) => s.adapter.docUnitIds().length === 2),
    null,
    { timeout: 15_000 },
  );
  await page.waitForTimeout(400);
  await expect(page.locator('#status')).toHaveText('New workbook · All changes calculated · Version 0');
  await expect(page.locator('#issues-toggle')).toBeHidden();
  await expect(page.locator('#formula-issues')).toBeHidden();
  await typeInto(page, 'C1', 'pear');
  await typeInto(page, 'D1', '3');
  await typeInto(page, 'E1', '=XLOOKUP("p*",C1:C2,D1:D2,,2)');
  await expect(page.locator('#issues-toggle')).toBeVisible();
  await expect(page.locator('#formula-issue-count')).toHaveText('1');
  await page.locator('#issues-toggle').click();
  await expect(page.locator('#formula-issues')).toBeVisible();
  await expect(page.locator('#panel-title')).toHaveText('Formula issues');
  await expect(page.locator('#formula-issue-list')).toContainText('Sheet1!E1');
  await expect(page.locator('#formula-issue-list')).toContainText('match_mode');

  // A function outside the catalog (BESSELJ is an Excel function Forge does
  // not implement) is refused the same way, and its cell stays blank.
  await typeInto(page, 'F1', '=BESSELJ(D1,1)');
  await expect(page.locator('#formula-issue-count')).toHaveText('2');
  await expect(page.locator('#formula-issue-list')).toContainText('Sheet1!F1');
  await expect(page.locator('#formula-issue-list')).toContainText('unsupported function BESSELJ');
  expect(await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.cell('Sheet1', 'F1')))).toEqual({
    raw: null,
    displayed: null,
    formula: '=BESSELJ(D1,1)',
  });

  // Clicking an issue moves the selection to its cell.
  await clickCell(page, 'A1');
  await page.locator('#formula-issue-list .issue', { hasText: 'Sheet1!E1' }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.formulaBarText()))).toBe('=XLOOKUP("p*",C1:C2,D1:D2,,2)');

  // The panel narrows the grid, so cells are found again from the canvas.
  await typeInto(page, 'E1', '=XLOOKUP("pear",C1:C2,D1:D2)');
  await expect(page.locator('#formula-issue-count')).toHaveText('1');
  await typeInto(page, 'F1', '=D1*2');
  await expect(page.locator('#issues-toggle')).toBeHidden();
  await expect(page.locator('#formula-issues')).toBeHidden();
  await expect(page.locator('#panel')).toBeHidden();
  // Independent arithmetic: D1 = 3, so the lookup is 3 and F1 = 3 * 2 = 6.
  expect(await page.evaluate(() => (window as any).__spike.then((s: any) => [s.adapter.cell('Sheet1', 'E1').raw, s.adapter.cell('Sheet1', 'F1').raw]))).toEqual([3, 6]);
  await expect(page.locator('#status')).toHaveText(/^New workbook · All changes calculated · Version \d+$/);
});

// A workbook's look comes through: number formats, bold, colors, alignment
// and column widths. Expected values are read from the fixture's XML
// (fixtures/corpus/op_styles_number_formats.xlsx), not from either engine:
//   <col min=1 width=18>, <col min=2 width=22>
//   A1 s=1: bold, font color FF1F4E79
//   B1 s=2: 45351 as yyyy-mm-dd (45292 is 2024-01-01, plus 59 days), fill FFFFF2CC, right
//   B4 s=5: 0.1275 as 0.00%;  B5: 1234.5 as "$"#,##0.00
//   B6: -42.75 as #,##0.00;[Red]-#,##0.00;  B10: 9876543.21 as #,##0, (thousands)
//   B8: 0.375 as # ?/?;  B11: 1.75 days as [h]:mm
test('display: an opened workbook keeps its number formats, cell styles and column widths, and exports the same values', async ({ page }) => {
  const errors = await boot(page, 'not-execute');
  await page.locator('#file').setInputFiles('../../fixtures/corpus/op_styles_number_formats.xlsx');
  await expect(page.locator('#status')).toHaveText('op_styles_number_formats.xlsx · All changes calculated · Version 0');
  await expect(page).toHaveTitle('op_styles_number_formats.xlsx · Workbook Forge');
  const seen = await page.evaluate(() =>
    (window as any).__spike.then((s: any) => {
      const sheet = s.host.univerAPI.getActiveWorkbook().getActiveSheet();
      const shown = (address: string) => sheet.getRange(address).getDisplayValue();
      return {
        sheet: sheet.getSheetName(),
        widths: [sheet.getColumnWidth(0), sheet.getColumnWidth(1), sheet.getColumnWidth(2)],
        a1: sheet.getRange('A1').getCellStyleData(),
        b1: sheet.getRange('B1').getCellStyleData(),
        shown: ['B1', 'B4', 'B5', 'B6', 'B8', 'B10', 'B11'].map(shown),
        forge: ['B1', 'B4', 'B6'].map((address) => s.adapter.cell('Formats', address).raw),
        divergence: s.adapter.divergence(),
        notices: s.adapter.notices,
      };
    }),
  );
  expect(seen.sheet).toBe('Formats');
  // Excel column widths are in characters; the view draws 7px per character.
  // Column C has no width in the file and keeps the default 88px.
  expect(seen.widths).toEqual([18 * 7, 22 * 7, 88]);
  expect(seen.a1).toEqual({ bl: 1, cl: { rgb: '#1F4E79' } });
  expect(seen.b1).toEqual({ n: { pattern: 'yyyy-mm-dd' }, bg: { rgb: '#FFF2CC' }, ht: 3 });
  // '# ?/?' pads the absent whole-number part with the space from the code.
  expect(seen.shown).toEqual(['2024-02-29', '12.75%', '$1,234.50', '-42.75', ' 3/8', '9,877', '42:00']);
  // The format changes what is drawn, never the value Forge holds.
  expect(seen.forge).toEqual([45351, 0.1275, -42.75]);
  expect(seen.divergence).toEqual([]);
  expect(seen.notices).toEqual([]);
  expect(errors).toEqual([]);
});

// The status bar totals the selection from Forge's values. Fixture row 1 is
// 10, 20, 25, 75, 55, 185: A1:C1 sums to 55 over 3 cells (average 55/3 =
// 18.3333333333 at twelve significant digits); one cell shows no totals.
test('status bar: a selection shows Average, Count and Sum, and follows edits and a new workbook', async ({ page }) => {
  await boot(page, 'not-execute');
  const stats = page.locator('#selection-stats');
  await clickCell(page, 'A1');
  await expect(stats).toHaveText('');
  await page.keyboard.down('Shift');
  await clickCell(page, 'C1');
  await page.keyboard.up('Shift');
  await expect(stats).toHaveText('Average 18.3333333333Count 3Sum 55');
  // A1 = 20 makes B1 = 40 and C1 = 45: sum 105, average 35.
  await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.edit([{ sheet: 'Sheet1', address: 'A1', value: 20 }], 'totals')));
  await expect(stats).toHaveText('Average 35Count 3Sum 105');
  await page.locator('#new').click();
  await expect(page.locator('#status')).toHaveText('New workbook · All changes calculated · Version 0');
  await expect(stats).toHaveText('');
});

// The Functions panel lists exactly the catalog's implemented functions, and
// the in-cell suggestions offer the same set in the same words.
test('functions: the panel lists the catalog, search narrows it, and suggestions offer only what Forge calculates', async ({ page }) => {
  const catalog = JSON.parse(readFileSync('../../catalog/formulas.json', 'utf8')) as { functions: { name: string }[] };
  const inventory = JSON.parse(readFileSync('../../catalog/function_inventory.json', 'utf8')) as { functions: unknown[] };
  const total = catalog.functions.length;
  await boot(page, 'not-execute');
  await expect(page.locator('#panel')).toBeHidden();
  await page.locator('#functions-toggle').click();
  await expect(page.locator('#functions')).toBeVisible();
  await expect(page.locator('#functions-toggle')).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#function-coverage')).toContainText(`${total} of the ${inventory.functions.length} functions`);
  await expect(page.locator('#function-count')).toHaveText(`${total} functions`);
  const listed = await page.locator('#function-list .fn code b').allTextContents();
  expect([...listed].sort()).toEqual(catalog.functions.map((f) => f.name).sort());

  await page.locator('#function-search').fill('xlookup');
  await expect(page.locator('#function-count')).toHaveText(`1 of ${total} functions`);
  await expect(page.locator('#function-list .fn:visible')).toHaveCount(1);
  await expect(page.locator('#function-list .fn:visible')).toContainText('XLOOKUP(lookup_value, lookup_array, return_array, [if_not_found], [match_mode], [search_mode])');
  await expect(page.locator('#function-list .fn:visible')).toContainText('Exact match only');
  await page.locator('#function-search').fill('no such function');
  await expect(page.locator('#function-count')).toHaveText(`0 of ${total} functions`);
  await expect(page.locator('#function-empty')).toBeVisible();
  await page.locator('#panel-close').click();
  await expect(page.locator('#panel')).toBeHidden();
  await expect(page.locator('#functions-toggle')).toHaveAttribute('aria-expanded', 'false');

  // Typing a formula: XLOOKUP is offered with Forge's summary. The BESSEL
  // functions, which Univer knows and Forge does not implement, are not
  // offered at all.
  expect(catalog.functions.filter((f) => f.name.startsWith('BESSEL'))).toEqual([]);
  const suggestions = page.locator('[data-u-comp="sheets-formula-editor"]');
  await clickCell(page, 'C3');
  await page.keyboard.type('=xl', { delay: 30 });
  await expect(suggestions).toContainText('XLOOKUP');
  await expect(suggestions).toContainText('Finds a value in one range and returns the matching item from another.');
  await page.keyboard.type('ookup(10,a1:c1,a1:c1)', { delay: 15 });
  // Univer refreshes the list 300 ms after the last keystroke, and while it
  // is open Enter picks a suggestion instead of committing. Past the opening
  // bracket the list closes on its own.
  await expect(suggestions).toHaveCount(0);
  await page.keyboard.press('Enter');
  // Independent: 10 is A1, so the lookup returns A1 = 10.
  await expect.poll(() => page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.cell('Sheet1', 'C3').raw))).toBe(10);
  await clickCell(page, 'C4');
  await page.keyboard.type('=bessel', { delay: 30 });
  await page.waitForTimeout(500);
  await expect(suggestions).toHaveCount(0);
  await page.keyboard.press('Escape');
});

// A formula that returns several cells is not spilled into the grid. The cell
// stays blank, a notice says so, and selecting it previews the values.
// SEQUENCE(3,2) is 1..6 filled row by row (Microsoft's SEQUENCE definition).
test('array result: the cell stays blank and selecting it previews the values', async ({ page }) => {
  await boot(page, 'not-execute');
  await page.locator('#new').click();
  await page.waitForFunction(
    () => (window as any).__spike.then((s: any) => s.adapter.docUnitIds().length === 2),
    null,
    { timeout: 15_000 },
  );
  await page.waitForTimeout(400);
  await typeInto(page, 'B2', '=SEQUENCE(3,2)');
  await expect(page.locator('#notices')).toHaveText('Sheet1!B2 returns 3 by 2 values. The grid shows one value per cell, so the cell stays blank. Select it to preview the result.');
  await expect(page.locator('#notice')).toHaveAttribute('data-kind', 'info');
  // Enter moved the selection to B3, which has no array.
  await expect(page.locator('#array-chip')).toBeHidden();
  await clickCell(page, 'B2');
  await expect(page.locator('#array-chip')).toHaveText('Array result: 3 by 2');
  await expect(page.locator('#array-result')).toBeVisible();
  await expect(page.locator('#panel-title')).toHaveText('Array result');
  expect(await page.locator('#array-table tr').evaluateAll((rows) => rows.map((row) => [...row.children].map((cell) => cell.textContent)))).toEqual([
    ['1', '2'],
    ['3', '4'],
    ['5', '6'],
  ]);
  const grid = await page.evaluate(() =>
    (window as any).__spike.then((s: any) => ['B2', 'C2', 'B3', 'C3', 'B4', 'C4'].map((address) => s.adapter.cell('Sheet1', address).displayed)),
  );
  expect(grid, 'nothing is placed into the grid').toEqual([null, null, null, null, null, null]);
  // Moving off the cell closes the preview.
  await clickCell(page, 'A1');
  await expect(page.locator('#array-chip')).toBeHidden();
  await expect(page.locator('#panel')).toBeHidden();
});

// The theme follows the device and can be switched for the visit. Nothing is
// written to browser storage: the page keeps no state between visits.
test('theme: the toggle switches the frame and the grid together and stores nothing', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await boot(page, 'not-execute');
  const theme = () => page.evaluate(() => ({ frame: document.documentElement.dataset.theme, grid: document.documentElement.classList.contains('univer-dark') }));
  expect(await theme()).toEqual({ frame: 'dark', grid: true });
  await expect(page.locator('#theme-toggle')).toHaveAttribute('aria-label', 'Switch to light theme');
  await page.locator('#theme-toggle').click();
  expect(await theme()).toEqual({ frame: 'light', grid: false });
  await page.locator('#theme-toggle').click();
  expect(await theme()).toEqual({ frame: 'dark', grid: true });
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, '']);
  // Univer's interface package opens an IndexedDB database of its own when the
  // page loads. Nothing in this shell writes to it, so every store in every
  // database must be empty: no workbook data and no preference.
  const stored = await page.evaluate(async () => {
    const counts: Record<string, number> = {};
    for (const { name } of await indexedDB.databases()) {
      if (!name) continue;
      const db = await new Promise<IDBDatabase>((resolve, reject) => {
        const request = indexedDB.open(name);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      for (const store of Array.from(db.objectStoreNames)) {
        counts[`${name}/${store}`] = await new Promise<number>((resolve, reject) => {
          const request = db.transaction(store, 'readonly').objectStore(store).count();
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        });
      }
      db.close();
    }
    return counts;
  });
  expect(Object.values(stored).every((count) => count === 0), JSON.stringify(stored)).toBe(true);
});

// ---- everyday editing: clear, paste and fill ----------------------------
//
// Each action is one Forge edit, so one step of undo. Expected values are
// hand arithmetic on the chain fixture (A1=10, B1=A1*2=20, C1=B1+5=25,
// D1=C1*3=75, E1=D1-B1=55, F1=SUM(A1:E1)=185) or Microsoft's own examples:
//   "Switch between relative, absolute, and mixed references"
//   https://support.microsoft.com/en-us/office/switch-between-relative-absolute-and-mixed-references-dfec08cd-ae65-4f56-839e-5f0d8d0baca9
//   "Fill data automatically in worksheet cells"
//   https://support.microsoft.com/en-us/office/fill-data-automatically-in-worksheet-cells-74e31bdd-d993-45da-aa82-35a236c5b5db
//   "Enter a series of numbers, dates, or other items"
//   https://support.microsoft.com/en-us/office/enter-a-series-of-numbers-dates-or-other-items-41e0bbf2-7198-4d78-8545-fdd4709976b4
//   "Fill a formula down into adjacent cells"
//   https://support.microsoft.com/en-us/office/fill-a-formula-down-into-adjacent-cells-041edfe2-05bc-40e6-b933-ef48c3f308c6

/** `[formula, value]` for each address; a blank cell is `[null, null]`. */
async function cellsOf(page: Page, addresses: string[]) {
  return page.evaluate(
    (list) =>
      (window as any).__spike.then((s: any) =>
        Object.fromEntries(list.map((a: string) => [a, [s.adapter.cell('Sheet1', a).formula, s.adapter.cell('Sheet1', a).raw]])),
      ),
    addresses,
  ) as Promise<Record<string, [string | null, unknown]>>;
}

async function editState(page: Page) {
  return page.evaluate(() =>
    (window as any).__spike.then((s: any) => ({
      revision: Number(s.adapter.revision),
      selection: s.host.univerAPI.getActiveWorkbook().getActiveSheet().getSelection().getActiveRange().getA1Notation() as string,
      notice: (document.querySelector('#notices')?.textContent ?? '') as string,
      blocked: s.adapter.audit.blocked as string[],
      divergence: s.adapter.divergence() as string[],
      foreignWrites: s.adapter.audit.foreignWrites as string[],
    })),
  );
}

async function cellCentre(page: Page, address: string) {
  const origin = await gridOrigin(page);
  return { x: origin.x + COLS[address[0]], y: origin.y + ROW1_CENTRE + (Number(address.slice(1)) - 1) * 24 };
}

/** Select one cell, or a range by clicking one corner and shift-clicking the other. */
async function selectCells(page: Page, from: string, to = from) {
  // Two quick clicks on one cell would open its editor, so let any earlier click age first.
  await page.waitForTimeout(550);
  await clickCell(page, from);
  if (to !== from) {
    await page.keyboard.down('Shift');
    await clickCell(page, to);
    await page.keyboard.up('Shift');
  }
  await page.waitForTimeout(250);
}

// The fill handle is the small square on the selection's bottom-right corner:
// 44px right of and 12px below the centre of an 88 by 24 cell.
async function fillHandle(page: Page, corner: string) {
  const centre = await cellCentre(page, corner);
  return { x: centre.x + 43, y: centre.y + 11 };
}

/** Drag the fill handle of a selection whose bottom-right cell is `corner` to the cell `to`. */
async function dragFillHandle(page: Page, corner: string, to: string) {
  const handle = await fillHandle(page, corner);
  const target = await cellCentre(page, to);
  await page.mouse.move(handle.x, handle.y);
  await page.waitForTimeout(120);
  await page.mouse.down();
  await page.mouse.move((handle.x + target.x) / 2, (handle.y + target.y) / 2, { steps: 6 });
  await page.mouse.move(target.x, target.y, { steps: 6 });
  await page.waitForTimeout(120);
  await page.mouse.up();
  await settle(page);
}

async function pasteText(page: Page, address: string, text: string) {
  await page.evaluate((value) => navigator.clipboard.writeText(value), text);
  await selectCells(page, address);
  await page.keyboard.press('Control+V');
  await settle(page);
}

async function copyCells(page: Page, from: string, to = from) {
  await selectCells(page, from, to);
  await page.keyboard.press('Control+C');
  await page.waitForTimeout(250);
}

const BLANK: [null, null] = [null, null];

test.describe('everyday editing: clear, paste and fill', () => {
  // The page writes to the clipboard when it copies. It is never allowed to
  // read it: a paste arrives in the browser's own paste event.
  test.use({ permissions: ['clipboard-write'] });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      (window as any).__clipboardReads = 0;
      for (const name of ['read', 'readText'] as const) {
        const original = (Clipboard.prototype as any)[name];
        (Clipboard.prototype as any)[name] = function (...args: unknown[]) {
          (window as any).__clipboardReads += 1;
          return original.apply(this, args);
        };
      }
    });
  });

  test.afterEach(async ({ page }) => {
    const state = await editState(page);
    expect(state.divergence, 'Univer shows what Forge holds').toEqual([]);
    expect(state.foreignWrites, 'cell writes not from the projection').toEqual([]);
    expect(await page.evaluate(() => (window as any).__clipboardReads), 'clipboard reads asked for').toBe(0);
    expectUniverNeverCalculated(await view(page));
  });

  test('Delete clears the selected cells as one step and Ctrl+Z brings them back', async ({ page }) => {
    const errors = await boot(page, 'not-execute');
    await selectCells(page, 'B1', 'C1');
    await page.keyboard.press('Delete');
    await settle(page);
    // B1 and C1 are empty, so D1 = 0*3 = 0, E1 = 0-0 = 0, F1 = 10+0+0+0+0 = 10.
    expect(await cellsOf(page, ['A1', 'B1', 'C1', 'D1', 'E1', 'F1'])).toEqual({
      A1: [null, 10], B1: BLANK, C1: BLANK, D1: ['=C1*3', 0], E1: ['=D1-B1', 0], F1: ['=SUM(A1:E1)', 10],
    });
    expect((await editState(page)).revision).toBe(1);
    const snapshot = (await forge(page)).snapshot.sheets[0].cells;
    expect(snapshot.B1?.formula ?? null).toBeNull();
    expect(snapshot.C1?.formula ?? null).toBeNull();

    await page.keyboard.press('Control+Z');
    await settle(page);
    const v = await view(page);
    expectDisplayed(v, INITIAL);
    expect(await cellsOf(page, ['B1', 'C1'])).toEqual({ B1: ['=A1*2', 20], C1: ['=B1+5', 25] });
    expect((await editState(page)).revision).toBe(2);

    // Nothing is held in H5:H6, so there is nothing to clear and nothing to undo.
    await selectCells(page, 'H5', 'H6');
    await page.keyboard.press('Delete');
    await settle(page);
    const after = await editState(page);
    expect(after.revision).toBe(2);
    expect(after.blocked).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('text pasted from another program lands as if typed, as one step', async ({ page }) => {
    const errors = await boot(page, 'not-execute');
    // Three rows by two columns; the last cell is empty.
    await pasteText(page, 'A2', '5\t=A2*2\n7\t=A3*2\nhello\t');
    expect(await cellsOf(page, ['A2', 'B2', 'A3', 'B3', 'A4', 'B4'])).toEqual({
      A2: [null, 5], B2: ['=A2*2', 10], A3: [null, 7], B3: ['=A3*2', 14], A4: [null, 'hello'], B4: BLANK,
    });
    let state = await editState(page);
    expect(state.revision).toBe(1);
    expect(state.selection).toBe('A2:B4');
    expectDisplayed(await view(page), INITIAL);

    // A quoted cell keeps its line break and stays one cell.
    await pasteText(page, 'A6', '"two\nlines"\t3');
    expect(await cellsOf(page, ['A6', 'B6', 'A7'])).toEqual({ A6: [null, 'two\nlines'], B6: [null, 3], A7: BLANK });
    expect((await editState(page)).revision).toBe(2);

    await page.keyboard.press('Control+Z');
    await settle(page);
    await page.keyboard.press('Control+Z');
    await settle(page);
    expect(await cellsOf(page, ['A2', 'B2', 'A3', 'B3', 'A4', 'A6', 'B6'])).toEqual({
      A2: BLANK, B2: BLANK, A3: BLANK, B3: BLANK, A4: BLANK, A6: BLANK, B6: BLANK,
    });
    state = await editState(page);
    expect(state.revision).toBe(4);
    expect(state.blocked).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('a copied formula moves its relative references and keeps its absolute ones', async ({ page }) => {
    const errors = await boot(page, 'not-execute');
    // Microsoft's table: copied two cells down and two cells to the right,
    // $A$1 stays $A$1, A$1 becomes C$1, $A1 becomes $A3 and A1 becomes C3.
    await typeInto(page, 'D3', '=$A$1+A$1+$A1+A1');
    expect(await cellsOf(page, ['D3'])).toEqual({ D3: ['=$A$1+A$1+$A1+A1', 40] }); // 10+10+10+10
    await copyCells(page, 'D3');
    await selectCells(page, 'F5');
    await page.keyboard.press('Control+V');
    await settle(page);
    // $A$1=10, C$1=25, $A3 and C3 are empty: 10+25+0+0 = 35.
    expect(await cellsOf(page, ['F5'])).toEqual({ F5: ['=$A$1+C$1+$A3+C3', 35] });
    expect((await editState(page)).revision).toBe(2);

    // Microsoft's example: =B4*C4 copied from D4 to D5 becomes =B5*C5, and
    // =$B$4*$C$4 copied the same way stays exactly the same.
    await typeInto(page, 'B4', '3');
    await typeInto(page, 'C4', '4');
    await typeInto(page, 'D4', '=B4*C4');
    await typeInto(page, 'E4', '=$B$4*$C$4');
    await copyCells(page, 'D4', 'E4');
    await selectCells(page, 'D5');
    await page.keyboard.press('Control+V');
    await settle(page);
    expect(await cellsOf(page, ['D4', 'E4', 'D5', 'E5'])).toEqual({
      D4: ['=B4*C4', 12], E4: ['=$B$4*$C$4', 12], D5: ['=B5*C5', 0], E5: ['=$B$4*$C$4', 12],
    });
    let state = await editState(page);
    expect(state.revision).toBe(7);
    expect(state.selection).toBe('D5:E5');

    // A copy is what the cells held when they were copied.
    await copyCells(page, 'B1');
    await typeInto(page, 'B1', '99');
    await selectCells(page, 'B8');
    await page.keyboard.press('Control+V');
    await settle(page);
    expect(await cellsOf(page, ['B1', 'B8'])).toEqual({ B1: [null, 99], B8: ['=A8*2', 0] });

    // One step of undo takes the whole paste back.
    await page.keyboard.press('Control+Z');
    await settle(page);
    expect(await cellsOf(page, ['B8'])).toEqual({ B8: BLANK });
    state = await editState(page);
    expect(state.revision).toBe(10);
    expect(state.blocked).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('Ctrl+R and Ctrl+D copy across and down, and the browser does not see the keys', async ({ page }) => {
    const errors = await boot(page, 'not-execute');
    await page.evaluate(() => {
      (window as any).__shortcuts = [];
      window.addEventListener('keydown', (event) => {
        if (event.ctrlKey && /^[dr]$/i.test(event.key)) (window as any).__shortcuts.push([event.key.toLowerCase(), event.defaultPrevented]);
      });
    });
    await selectCells(page, 'F1', 'G1');
    await page.keyboard.press('Control+R');
    await settle(page);
    // G1 = SUM(B1:F1) = 20+25+75+55+185 = 360.
    expect(await cellsOf(page, ['G1'])).toEqual({ G1: ['=SUM(B1:F1)', 360] });
    expect((await editState(page)).revision).toBe(1);

    await selectCells(page, 'A1', 'F3');
    await page.keyboard.press('Control+D');
    await settle(page);
    // Rows 2 and 3 repeat row 1: 10, 20, 25, 75, 55, 185.
    for (const row of [2, 3]) {
      expect(await cellsOf(page, ['A', 'B', 'C', 'D', 'E', 'F'].map((col) => `${col}${row}`))).toEqual({
        [`A${row}`]: [null, 10],
        [`B${row}`]: [`=A${row}*2`, 20],
        [`C${row}`]: [`=B${row}+5`, 25],
        [`D${row}`]: [`=C${row}*3`, 75],
        [`E${row}`]: [`=D${row}-B${row}`, 55],
        [`F${row}`]: [`=SUM(A${row}:E${row})`, 185],
      });
    }
    let state = await editState(page);
    expect(state.revision).toBe(2);
    expect(state.selection).toBe('A1:F3');

    await page.keyboard.press('Control+Z');
    await settle(page);
    expect(await cellsOf(page, ['A2', 'F2', 'A3', 'F3', 'G1'])).toEqual({ A2: BLANK, F2: BLANK, A3: BLANK, F3: BLANK, G1: ['=SUM(B1:F1)', 360] });
    state = await editState(page);
    expect(state.revision).toBe(3);
    expect(state.blocked).toEqual([]);
    // Ctrl+R reloads the page and Ctrl+D bookmarks it unless the page takes the key.
    expect(await page.evaluate(() => (window as any).__shortcuts)).toEqual([['r', true], ['d', true]]);
    expect(errors).toEqual([]);
  });

  test('the fill handle copies formulas down, right, up and left, and a refused drag changes nothing', async ({ page }) => {
    const errors = await boot(page, 'not-execute');
    await pasteText(page, 'A2', '3\n4');
    await selectCells(page, 'B1');
    await dragFillHandle(page, 'B1', 'B3');
    // B2 = A2*2 = 6, B3 = A3*2 = 8.
    expect(await cellsOf(page, ['B2', 'B3', 'B4'])).toEqual({ B2: ['=A2*2', 6], B3: ['=A3*2', 8], B4: BLANK });
    let state = await editState(page);
    expect(state.revision).toBe(2);
    expect(state.selection).toBe('B1:B3');

    await selectCells(page, 'F1');
    await dragFillHandle(page, 'F1', 'H1');
    // G1 = SUM(B1:F1) = 360, H1 = SUM(C1:G1) = 25+75+55+185+360 = 700.
    expect(await cellsOf(page, ['G1', 'H1', 'I1'])).toEqual({ G1: ['=SUM(B1:F1)', 360], H1: ['=SUM(C1:G1)', 700], I1: BLANK });
    state = await editState(page);
    expect(state.revision).toBe(3);
    expect(state.selection).toBe('F1:H1');

    await typeInto(page, 'C5', '=A5+1');
    await selectCells(page, 'C5');
    await dragFillHandle(page, 'C5', 'C3');
    // C4 = A4+1 = 0+1 = 1, C3 = A3+1 = 4+1 = 5.
    expect(await cellsOf(page, ['C2', 'C3', 'C4', 'C5'])).toEqual({ C2: BLANK, C3: ['=A3+1', 5], C4: ['=A4+1', 1], C5: ['=A5+1', 1] });
    state = await editState(page);
    expect(state.revision).toBe(5);
    expect(state.selection).toBe('C3:C5');

    await typeInto(page, 'B7', '2');
    await typeInto(page, 'E7', '=D7*2');
    await selectCells(page, 'E7');
    await dragFillHandle(page, 'E7', 'C7');
    // C7 = B7*2 = 4, D7 = C7*2 = 8, E7 = D7*2 = 16.
    expect(await cellsOf(page, ['B7', 'C7', 'D7', 'E7'])).toEqual({ B7: [null, 2], C7: ['=B7*2', 4], D7: ['=C7*2', 8], E7: ['=D7*2', 16] });
    state = await editState(page);
    expect(state.revision).toBe(8);
    expect(state.selection).toBe('C7:E7');

    // =A5+1 one column to the left would point before column A: refused whole.
    await selectCells(page, 'C5');
    await dragFillHandle(page, 'C5', 'B5');
    expect(await cellsOf(page, ['B5', 'C5'])).toEqual({ B5: BLANK, C5: ['=A5+1', 1] });
    state = await editState(page);
    expect(state.revision).toBe(8);
    expect(state.notice).toBe('Change not applied. The formula in C5 cannot be copied: at B5 it would point outside the sheet.');

    // The next drag works, and one undo takes it back.
    await selectCells(page, 'H1');
    await dragFillHandle(page, 'H1', 'H2');
    // H2 = SUM(C2:G2); those cells are empty.
    expect(await cellsOf(page, ['H2'])).toEqual({ H2: ['=SUM(C2:G2)', 0] });
    expect((await editState(page)).revision).toBe(9);
    await page.keyboard.press('Control+Z');
    await settle(page);
    expect(await cellsOf(page, ['H1', 'H2'])).toEqual({ H1: ['=SUM(C1:G1)', 700], H2: BLANK });
    expect((await editState(page)).revision).toBe(10);
    expect(errors).toEqual([]);
  });

  test('a double click on the fill handle fills down, dragging it back clears, and a click on it does nothing', async ({ page }) => {
    const errors = await boot(page, 'not-execute');
    await pasteText(page, 'A2', '3\n4\n5');
    await selectCells(page, 'B1');
    const handle = await fillHandle(page, 'B1');
    await page.mouse.dblclick(handle.x, handle.y);
    await settle(page);
    // Down as far as column A has entries: B2 = 6, B3 = 8, B4 = 10.
    expect(await cellsOf(page, ['B2', 'B3', 'B4', 'B5'])).toEqual({ B2: ['=A2*2', 6], B3: ['=A3*2', 8], B4: ['=A4*2', 10], B5: BLANK });
    let state = await editState(page);
    expect(state.revision).toBe(2);
    expect(state.selection).toBe('B1:B4');

    // Dragging the handle back over the selection clears what it gives up.
    await selectCells(page, 'B1', 'B4');
    await dragFillHandle(page, 'B4', 'B2');
    expect(await cellsOf(page, ['B1', 'B2', 'B3', 'B4'])).toEqual({ B1: ['=A1*2', 20], B2: ['=A2*2', 6], B3: BLANK, B4: BLANK });
    state = await editState(page);
    expect(state.revision).toBe(3);
    expect(state.selection).toBe('B1:B2');

    // The grid toolkit remembers a drag that changed nothing (here, one back
    // over empty cells); a click on another cell's handle must not replay it.
    await selectCells(page, 'H5', 'H7');
    await dragFillHandle(page, 'H7', 'H5');
    state = await editState(page);
    expect(state.revision).toBe(3);
    expect(state.selection).toBe('H5');
    await selectCells(page, 'C1');
    const other = await fillHandle(page, 'C1');
    await page.mouse.click(other.x, other.y);
    await settle(page);
    expect(await cellsOf(page, ['C1', 'C2', 'B3'])).toEqual({ C1: ['=B1+5', 25], C2: BLANK, B3: BLANK });
    state = await editState(page);
    expect(state.revision).toBe(3);
    expect(state.selection).toBe('C1');
    expect(state.blocked).toEqual([]);

    await page.keyboard.press('Control+Z');
    await settle(page);
    expect(await cellsOf(page, ['B3', 'B4'])).toEqual({ B3: ['=A3*2', 8], B4: ['=A4*2', 10] });
    await page.keyboard.press('Control+Z');
    await settle(page);
    expect(await cellsOf(page, ['B2', 'B3', 'B4'])).toEqual({ B2: BLANK, B3: BLANK, B4: BLANK });
    expect((await editState(page)).revision).toBe(5);
    expect(errors).toEqual([]);
  });

  test('a formula cell clicked while another cell is being edited keeps its outline and fill handle', async ({ page }) => {
    const errors = await boot(page, 'not-execute');
    // Open an editor with a double click and leave it by clicking a formula
    // cell. The grid toolkit used to erase that cell's outline, so the first
    // drag from its corner selected cells instead of filling them.
    await selectCells(page, 'H5');
    const h5 = await cellCentre(page, 'H5');
    await page.mouse.dblclick(h5.x, h5.y);
    await page.waitForTimeout(400);
    await selectCells(page, 'F1');
    await dragFillHandle(page, 'F1', 'F3');
    expect(await cellsOf(page, ['F2', 'F3'])).toEqual({ F2: ['=SUM(A2:E2)', 0], F3: ['=SUM(A3:E3)', 0] });
    let state = await editState(page);
    expect(state.revision).toBe(1);
    expect(state.selection).toBe('F1:F3');

    // The everyday case: type a value, then click a formula cell.
    await selectCells(page, 'A2');
    await page.keyboard.type('4');
    await selectCells(page, 'B1');
    await settle(page);
    await dragFillHandle(page, 'B1', 'B2');
    expect(await cellsOf(page, ['A2', 'B2', 'F2'])).toEqual({ A2: [null, 4], B2: ['=A2*2', 8], F2: ['=SUM(A2:E2)', 12] });
    state = await editState(page);
    expect(state.revision).toBe(3);
    expect(state.selection).toBe('B1:B2');

    // A formula still being typed hides the outline as before and commits normally.
    await selectCells(page, 'H8');
    await page.keyboard.type('=A1+');
    await clickCell(page, 'B1');
    await page.keyboard.press('Enter');
    await settle(page);
    expect(await cellsOf(page, ['H8'])).toEqual({ H8: ['=A1+B1', 30] });
    expect((await editState(page)).blocked).toEqual([]);
    expect(errors).toEqual([]);
  });

  test('the fill handle refuses what Excel would continue as a series and copies a single number', async ({ page }) => {
    const errors = await boot(page, 'not-execute');
    const series = 'Fill copies formulas and repeated values. Continuing a series (1, 2, 3, dates, Item 1, Monday) is not available yet. Ctrl+D and Ctrl+R copy as they are.';
    // Microsoft: two numbers set a pattern (1, 3 continues 5, 7, 9).
    await pasteText(page, 'H1', '1\n3');
    await selectCells(page, 'H1', 'H2');
    await dragFillHandle(page, 'H2', 'H4');
    expect(await cellsOf(page, ['H1', 'H2', 'H3', 'H4'])).toEqual({ H1: [null, 1], H2: [null, 3], H3: BLANK, H4: BLANK });
    let state = await editState(page);
    expect(state.revision).toBe(1);
    expect(state.notice).toBe(series);
    expect(state.blocked.at(-1)).toBe('sheet.command.auto-fill');
    await page.locator('#notice-close').click();

    // Microsoft: Item1 continues Item2, Item3.
    await typeInto(page, 'I1', 'Item 1');
    await selectCells(page, 'I1');
    await dragFillHandle(page, 'I1', 'I3');
    expect(await cellsOf(page, ['I1', 'I2', 'I3'])).toEqual({ I1: [null, 'Item 1'], I2: BLANK, I3: BLANK });
    state = await editState(page);
    expect(state.revision).toBe(2);
    expect(state.notice).toBe(series);
    await page.locator('#notice-close').click();

    // Microsoft: "For the series 2, 2, 2, 2..., type 2 in first cell only."
    await typeInto(page, 'G1', '2');
    await selectCells(page, 'G1');
    await dragFillHandle(page, 'G1', 'G3');
    expect(await cellsOf(page, ['G1', 'G2', 'G3', 'G4'])).toEqual({ G1: [null, 2], G2: [null, 2], G3: [null, 2], G4: BLANK });
    expect((await editState(page)).revision).toBe(4);

    // Ctrl+D copies the top cell down whatever it holds.
    await selectCells(page, 'H1', 'H3');
    await page.keyboard.press('Control+D');
    await settle(page);
    expect(await cellsOf(page, ['H1', 'H2', 'H3', 'H4'])).toEqual({ H1: [null, 1], H2: [null, 1], H3: [null, 1], H4: BLANK });
    expect((await editState(page)).revision).toBe(5);
    expect(errors).toEqual([]);
  });

  test('cut is refused, and a paste whose formula would leave the sheet changes nothing', async ({ page }) => {
    const errors = await boot(page, 'not-execute');
    await selectCells(page, 'B1');
    await page.keyboard.press('Control+X');
    await settle(page);
    let state = await editState(page);
    expect(state.notice).toBe('Cut is not available yet. Copy and paste, then clear the original with Delete.');
    expect(state.blocked.at(-1)).toMatch(/cut$/);
    expect(state.revision).toBe(0);
    await page.locator('#notice-close').click();

    // =A1*2 one column to the left would point before column A.
    await copyCells(page, 'B1');
    await selectCells(page, 'A3');
    await page.keyboard.press('Control+V');
    await settle(page);
    state = await editState(page);
    expect(state.notice).toBe('Change not applied. The formula in B1 cannot be copied: at A3 it would point outside the sheet.');
    expect(state.revision).toBe(0);
    expect(await cellsOf(page, ['A3', 'B1'])).toEqual({ A3: BLANK, B1: ['=A1*2', 20] });
    expectDisplayed(await view(page), INITIAL);
    expect(errors).toEqual([]);
  });

  test('a copy repeats over a selection it fits a whole number of times, and a long paste grows the grid', async ({ page }) => {
    const errors = await boot(page, 'not-execute');
    // A1:B1 (10 and =A1*2) into A3:D4: two across and two down.
    await copyCells(page, 'A1', 'B1');
    await selectCells(page, 'A3', 'D4');
    await page.keyboard.press('Control+V');
    await settle(page);
    for (const row of [3, 4]) {
      expect(await cellsOf(page, [`A${row}`, `B${row}`, `C${row}`, `D${row}`, `E${row}`])).toEqual({
        [`A${row}`]: [null, 10], [`B${row}`]: [`=A${row}*2`, 20], [`C${row}`]: [null, 10], [`D${row}`]: [`=C${row}*2`, 20], [`E${row}`]: BLANK,
      });
    }
    let state = await editState(page);
    expect(state.revision).toBe(1);
    expect(state.selection).toBe('A3:D4');
    await page.keyboard.press('Control+Z');
    await settle(page);
    expect(await cellsOf(page, ['A3', 'B3', 'C3', 'D3', 'A4', 'D4'])).toEqual({ A3: BLANK, B3: BLANK, C3: BLANK, D3: BLANK, A4: BLANK, D4: BLANK });
    expect((await editState(page)).revision).toBe(2);

    // Three columns is not a whole number of two-column copies: one copy at the top left.
    await selectCells(page, 'A6', 'C6');
    await page.keyboard.press('Control+V');
    await settle(page);
    expect(await cellsOf(page, ['A6', 'B6', 'C6'])).toEqual({ A6: [null, 10], B6: ['=A6*2', 20], C6: BLANK });
    state = await editState(page);
    expect(state.revision).toBe(3);
    expect(state.selection).toBe('A6:B6');

    // 300 rows from row 10: the last is row 309, and the grid keeps 100 rows beyond it.
    const lines = Array.from({ length: 300 }, (_, i) => `${i + 10}\t=A${i + 10}*2`).join('\n');
    await pasteText(page, 'A10', lines);
    expect(await cellsOf(page, ['A10', 'B10', 'A309', 'B309', 'A310'])).toEqual({
      A10: [null, 10], B10: ['=A10*2', 20], A309: [null, 309], B309: ['=A309*2', 618], A310: BLANK,
    });
    expect((await editState(page)).revision).toBe(4);
    const shape = await page.evaluate(() =>
      (window as any).__spike.then((s: any) => {
        const sheet = s.host.univerAPI.getActiveWorkbook().getActiveSheet();
        return { rows: sheet.getMaxRows(), cols: sheet.getMaxColumns() };
      }),
    );
    expect(shape).toEqual({ rows: 409, cols: 26 });
    expect(errors).toEqual([]);
  });
});

// A phone-width screen: every control stays reachable, nothing scrolls
// sideways, and the side panel covers the grid instead of squeezing it.
test('narrow screen: controls stay reachable at 375px and the panel overlays the grid', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 700 });
  await page.goto('/');
  await page.evaluate(() => (window as any).__spike.then(() => undefined));
  for (const id of ['undo', 'redo', 'new', 'export', 'functions-toggle', 'theme-toggle']) {
    const box = await page.locator(`#${id}`).boundingBox();
    expect(box, id).not.toBeNull();
    expect(box!.x, id).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width, id).toBeLessThanOrEqual(375);
    expect(box!.width, id).toBeGreaterThanOrEqual(32);
    expect(box!.height, id).toBeGreaterThanOrEqual(32);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(375);
  await expect(page.locator('#privacy')).toContainText('Your workbook stays on this device.');
  const before = await page.locator('#app').boundingBox();
  await page.locator('#functions-toggle').click();
  await expect(page.locator('#functions')).toBeVisible();
  expect((await page.locator('#app').boundingBox())!.width).toBe(before!.width);
  expect((await page.locator('#panel').boundingBox())!.width).toBe(375);
  await page.locator('#panel-close').click();
  await expect(page.locator('#panel')).toBeHidden();
});
