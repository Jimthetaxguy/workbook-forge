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
  // First click focuses Univer's canvas; the editor is ready after it.
  await clickCell(page, 'H5');
  await page.waitForTimeout(400);
  return errors;
}

// Row 1's centre sits 32px below the grid canvas top (20px column header plus
// half a 24px row).
const ROW1_CENTRE = 32;

// The grid canvas's page position. Measured rather than fixed because the
// notices above the grid wrap differently with the host's fonts, which moves
// the grid down by a row's height on some machines.
async function gridOrigin(page: Page) {
  return page.evaluate(() => {
    const rects = [...document.querySelectorAll('canvas')]
      .map((canvas) => canvas.getBoundingClientRect())
      .filter((rect) => rect.width > 0 && rect.height > 0);
    // Before Univer renders (or in modes without a grid) fall back to the
    // default 1280x800 layout, where the grid canvas starts at y=202.
    if (rects.length === 0) return { x: 0, y: 202 };
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
      for (const id of [
        'sheet.command.insert-row-before',
        'sheet.command.insert-col-before',
        'sheet.command.remove-row-confirm',
        'sheet.command.insert-sheet',
        'sheet.command.auto-fill',
        'univer.command.paste',
        'sheet.command.clear-selection-content',
      ]) {
        const result = await page.evaluate(
          (commandId) => (window as any).__spike.then((s: any) => s.host.univerAPI.executeCommand(commandId)),
          id,
        );
        expect(result, id).toBe(false);
        await expect(page.locator('#notices')).toContainText(`Blocked ${id}`);
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
      expect(shape).toEqual({ sheets: 1, rows: 20, cols: 10 });
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
      expect(v.notices.filter((n) => n.startsWith('Forge rejected'))).toEqual([]);
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
  expect(notices.filter((n: string) => n.startsWith('Forge rejected'))).toEqual([]);
  expect(errors).toEqual([]);
});
