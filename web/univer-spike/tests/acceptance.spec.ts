// Acceptance: Univer as a view, Forge (WASM in a Web Worker) as the only
// calculator and source of truth.
//
// Fixture: fixtures/univer-spike-chain.xlsx, A1 = 10 and five dependent
// formulas that already exist in the XLSX. Expected values are independent
// arithmetic, never engine output:
//   A1=10: B1=A1*2=20, C1=B1+5=25, D1=C1*3=75, E1=D1-B1=55, F1=SUM(A1:E1)=185
//   A1=20: 40, 45, 135, 95, 335
//   C1=B1+100 (A1=20): C1=140, D1=420, E1=380, F1=20+40+140+420+380=1000
import { expect, test, type Page } from '@playwright/test';

const ADDRESSES = ['A1', 'B1', 'C1', 'D1', 'E1', 'F1'] as const;
const INITIAL = [10, 20, 25, 75, 55, 185];
const AFTER_INPUT = [20, 40, 45, 135, 95, 335];
const AFTER_FORMULA = [20, 40, 140, 420, 380, 1000];
const FORMULAS = { B1: 'A1*2', C1: 'B1+5', D1: 'C1*3', E1: 'D1-B1', F1: 'SUM(A1:E1)' };

// Cell centres in the default 1280x800 layout (column width 88px, row 24px).
const COLS: Record<string, number> = { A: 90, B: 178, C: 266, D: 354, E: 442, F: 530, G: 618, H: 706 };

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

async function clickCell(page: Page, address: string) {
  await page.mouse.click(COLS[address[0]], 179 + (Number(address.slice(1)) - 1) * 24);
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

      // Undo: Univer's undo command is routed to Forge, which applies the
      // inverse edit at the expected revision (2 -> 3).
      await page.keyboard.press('Control+z');
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

      // An edit to a cell Forge does not hold is rejected by Forge, not
      // translated, and Univer keeps showing nothing there.
      await typeInto(page, 'H10', '7');
      const rejected = await view(page);
      expect(rejected.revision).toBe('0');
      expect(rejected.notices.at(-1)).toMatch(/Forge rejected cell edit: absent_cell/);
      await expect(page.locator('#notices')).toContainText('absent_cell');
      expect(rejected.cells.H10.raw).toBeNull();
      expectDisplayed(rejected, INITIAL);
      expectUniverNeverCalculated(rejected);
    });
  });
}

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
