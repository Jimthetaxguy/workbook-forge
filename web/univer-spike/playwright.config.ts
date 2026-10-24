import { defineConfig } from '@playwright/test';

// Playwright's bundled Chromium has no linux-arm64 build; CHROMIUM_PATH points
// at a system Chromium (e.g. /usr/bin/chromium) when needed.
const executablePath = process.env.CHROMIUM_PATH || undefined;

export default defineConfig({
  testDir: 'tests',
  timeout: 90_000,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4179',
    headless: true,
    viewport: { width: 1280, height: 800 },
    launchOptions: { executablePath, args: ['--no-sandbox'] },
  },
  webServer: process.env.SPIKE_EXTERNAL_SERVER ? undefined : {
    command: 'npx vite --port 4179 --strictPort --host 127.0.0.1',
    url: 'http://127.0.0.1:4179',
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
