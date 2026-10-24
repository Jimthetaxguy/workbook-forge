import { test } from '@playwright/test';
test('debug dom', async ({ page }) => {
  const logs: string[] = [];
  page.on('console', (m) => logs.push(`${m.type()}: ${m.text().slice(0, 200)}`));
  page.on('pageerror', (e) => logs.push(`pageerror: ${e.message}`));
  page.on('response', (r) => { if (r.status() >= 400) logs.push(`HTTP ${r.status()} ${r.url()}`); });
  await page.goto('/?mode=no-formula-plugins');
  await page.waitForFunction(() => (window as any).__spike, null, { timeout: 30000 });
  await page.evaluate(() => (window as any).__spike.then(() => undefined));
  await page.mouse.click(90, 179);
  await page.waitForTimeout(500);
  const info = await page.evaluate(() => ({
    active: document.activeElement?.outerHTML.slice(0, 200),
    editables: [...document.querySelectorAll('[contenteditable], textarea, input')].map((e) => e.outerHTML.slice(0, 150)),
    canvases: [...document.querySelectorAll('canvas')].map((c) => `${c.id}|${c.className}|${c.getBoundingClientRect().x},${c.getBoundingClientRect().y},${c.width}x${c.height}`),
  }));
  console.log(JSON.stringify(info, null, 1));
  console.log(logs.slice(0, 30).join('\n'));
});
