import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { copyFile, mkdir, mkdtemp, writeFile, symlink } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = fileURLToPath(new URL('..', import.meta.url));

async function listen(server, port = 0) {
  server.listen(port, '127.0.0.1');
  await once(server, 'listening');
  return server.address().port;
}

async function close(server) {
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
}

async function freePort() {
  const server = createServer();
  const port = await listen(server);
  await close(server);
  return port;
}

// Exercise the actual launcher and Playwright configuration with a real HTTP
// server. A tiny request-only test keeps browser/fixture failures out of this
// server-ownership regression. Retain the sandbox for failure diagnostics.
async function sandbox() {
  const directory = await mkdtemp(path.join(tmpdir(), 'forge-launcher-test-'));
  await Promise.all(['scripts', 'tests', 'dist'].map((name) => mkdir(path.join(directory, name))));
  await copyFile(path.join(root, 'scripts/e2e.sh'), path.join(directory, 'scripts/e2e.sh'));
  await copyFile(path.join(root, 'playwright.config.ts'), path.join(directory, 'playwright.config.ts'));
  await symlink(path.join(root, 'node_modules'), path.join(directory, 'node_modules'));
  await writeFile(path.join(directory, 'dist/index.html'), 'owned-build');
  await writeFile(path.join(directory, 'tests/server.spec.ts'), `
import { test, expect } from '@playwright/test';
test('serves this build', async ({ request }) => {
  const response = await request.get('/');
  expect(await response.text()).toBe(process.env.EXPECT_BODY || 'owned-build');
});
`);
  return directory;
}

async function run(directory, port, extra = {}, args = [], direct = false) {
  const env = { ...process.env, SPIKE_SKIP_BUILD: '1', SPIKE_PORT: String(port), ...extra };
  delete env.SPIKE_EXTERNAL_SERVER;
  const child = spawn(direct ? 'npx' : 'bash', direct ? ['playwright', 'test', ...args] : ['scripts/e2e.sh', ...args], {
    cwd: directory, env, stdio: ['ignore', 'pipe', 'pipe'], detached: true,
  });
  let output = '';
  child.stdout.on('data', (data) => { output += data; });
  child.stderr.on('data', (data) => { output += data; });
  const timer = setTimeout(() => {
    try { process.kill(-child.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
  }, 75_000);
  try {
    const [code, signal] = await once(child, 'close');
    assert.equal(signal, null, output);
    return { code, output };
  } finally {
    clearTimeout(timer);
  }
}

async function expectReleased(port) {
  const server = createServer();
  await listen(server, port);
  await close(server);
}

test('a nondefault port serves this build and is released after success', async () => {
  const port = await freePort();
  const result = await run(await sandbox(), port);
  assert.equal(result.code, 0, result.output);
  assert.match(result.output, /1 passed/);
  await expectReleased(port);
});

for (const direct of [false, true]) {
  test(`${direct ? 'direct Playwright' : 'shell launcher'} refuses an occupied port and preserves its server`, async () => {
    const server = createServer((_request, response) => response.end('owned-build'));
    const port = await listen(server);
    try {
      const result = await run(await sandbox(), port, {}, [], direct);
      assert.notEqual(result.code, 0, result.output);
      assert.match(result.output, /already used/);
      assert.equal(await (await fetch(`http://127.0.0.1:${port}`)).text(), 'owned-build');
    } finally {
      await close(server);
    }
  });
}

test('a failed test releases the server and preserves the failure exit code', async () => {
  const port = await freePort();
  const result = await run(await sandbox(), port, { EXPECT_BODY: 'intentional-failure' });
  assert.notEqual(result.code, 0, result.output);
  assert.match(result.output, /1 failed/);
  await expectReleased(port);
});

test('invalid ports are rejected before launching a server', async () => {
  const directory = await sandbox();
  for (const port of ['invalid', '0', '65536']) {
    const result = await run(directory, port, {}, ['--list']);
    assert.notEqual(result.code, 0, result.output);
    assert.match(result.output, /SPIKE_PORT must be a TCP port/);
  }
});

test('listing tests does not contact or replace an occupied server', async () => {
  let requests = 0;
  const server = createServer((_request, response) => { requests++; response.end('existing'); });
  const port = await listen(server);
  try {
    const result = await run(await sandbox(), port, {}, ['--list']);
    assert.equal(result.code, 0, result.output);
    assert.equal(requests, 0);
  } finally {
    await close(server);
  }
});
