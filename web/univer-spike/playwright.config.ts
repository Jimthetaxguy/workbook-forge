import { defineConfig } from '@playwright/test';

// Playwright's bundled Chromium has no linux-arm64 build; CHROMIUM_PATH points
// at a system Chromium (e.g. /usr/bin/chromium) when needed.
const executablePath = process.env.CHROMIUM_PATH || undefined;
const port = process.env.SPIKE_PORT || '4179';
if (!/^\d{1,5}$/.test(port) || Number(port) < 1 || Number(port) > 65_535) {
  throw new Error(`SPIKE_PORT must be a TCP port from 1 to 65535; received ${port}`);
}
const baseURL = `http://127.0.0.1:${port}`;
const build = process.env.SPIKE_SKIP_BUILD ? '' : 'npx vite build --logLevel error && ';

export default defineConfig({
  testDir: 'tests',
  timeout: 90_000,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL,
    headless: true,
    viewport: { width: 1280, height: 800 },
    launchOptions: { executablePath, args: ['--no-sandbox'] },
  },
  webServer: {
    command: `${build}python3 -m http.server ${port} --bind 127.0.0.1 --directory dist`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
