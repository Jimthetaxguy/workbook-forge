// Exploration: drive the real UI in one mode and print what happened.
// Not part of the acceptance gate; run with: bash scripts/e2e.sh tests/explore.spec.ts -g <mode>
import { test, type Page } from '@playwright/test';

async function boot(page: Page, mode: string) {
  await page.goto(`/?mode=${mode}`);
  await page.waitForFunction(() => (window as any).__spike, null, { timeout: 30000 });
  await page.evaluate(() => (window as any).__spike.then(() => undefined));
}
// Cell centres in the default 1280x800 layout (column width 88px, row 24px).
const COLS: Record<string, number> = { A: 90, B: 178, C: 266, D: 354, E: 442, F: 530, G: 618, H: 706 };
async function select(page: Page, address: string) {
  const col = address[0];
  const row = Number(address.slice(1));
  await page.mouse.click(COLS[col], 179 + (row - 1) * 24);
}
async function typeInto(page: Page, address: string, text: string) {
  await select(page, address);
  await page.keyboard.type(text, { delay: 20 });
  await page.keyboard.press('Enter');
  await page.waitForTimeout(400);
  await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.idle().then(() => undefined)));
}
async function state(page: Page) {
  return page.evaluate(async () => {
    const s = await (window as any).__spike;
    const cells = Object.fromEntries(['A1', 'B1', 'C1', 'D1', 'E1', 'F1'].map((a) => [a, s.adapter.cell('Sheet1', a)]));
    return { rev: s.adapter.revision, cells, audit: s.adapter.audit, div: s.adapter.divergence(), notices: s.adapter.notices };
  });
}
for (const mode of ['univer-default', 'load-only', 'not-execute', 'engine-only', 'no-formula-plugins']) {
  test(`explore ${mode}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await boot(page, mode);
    await select(page, 'H5');
    await page.waitForTimeout(500);
    await select(page, 'C1');
    await page.waitForTimeout(500);
    const bar = await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.formulaBarText()));
    await typeInto(page, 'A1', '20');
    const s1 = await state(page);
    await typeInto(page, 'C1', '=B1+100');
    const s2 = await state(page);
    await page.keyboard.press('Control+z');
    await page.waitForTimeout(500);
    const s3 = await state(page);
    const docs = await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.docUnitIds()));
    const trace = await page.evaluate(() => (window as any).__spike.then((s: any) => s.adapter.audit.trace.filter((t: string) => !t.startsWith('2:'))));
    await page.screenshot({ path: `test-results/explore-${mode}.png` });
    console.log(JSON.stringify({ mode, bar, docs, trace }));
    console.log(JSON.stringify({ mode, bar, s1, s2, s3, errors: errors.slice(0, 3) }));
  });
}
