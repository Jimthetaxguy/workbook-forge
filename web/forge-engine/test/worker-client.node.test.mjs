// Worker/client lifecycle tests (no WASM, no real Worker): a fake channel
// stands in for the Worker boundary so failure paths can be driven directly.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ForgeClient, ForgeClientError } from '../src/client.ts';
import { createWorkerHost } from '../src/worker-host.ts';

/** Messages cross asynchronously and are structured-cloned, like postMessage. */
function deliver(target, type, data) {
  setImmediate(() => target.dispatchEvent(Object.assign(new Event(type), { data: structuredClone(data) })));
}

/**
 * A client-side port wired to a worker host. `engine` is the promise the
 * worker awaits; the returned port can also raise worker `error` and
 * `messageerror` events.
 */
function channel(engine) {
  const port = new EventTarget();
  port.terminated = false;
  port.terminate = () => {
    port.terminated = true;
  };
  const host = createWorkerHost({
    engine,
    post: (message) => deliver(port, 'message', message),
  });
  port.postMessage = (message) => {
    const request = structuredClone(message);
    setImmediate(() => host.receive(request));
  };
  return { port, host };
}

/** An engine stub: `handle` maps a request to its value or throws. */
function fakeEngine(handle) {
  return {
    handle(request) {
      return { id: request.id, command: request.command, result: { ok: true, value: handle(request) } };
    },
    drainPatches: () => [],
    drainEvents: () => [],
  };
}

const neverSettles = () => new Promise(() => {});

test('init failing twice rejects pending and later sends instead of hanging', async () => {
  // The worker tries fetch-then-init and then plain init; both failing leaves
  // the engine promise rejected.
  const { port } = channel(Promise.reject(new Error('wasm fetch failed; fallback init failed')));
  const client = new ForgeClient(port);
  await assert.rejects(client.snapshot(), (error) => {
    assert.ok(error instanceof ForgeClientError);
    assert.equal(error.code, 'engine_init_failed');
    assert.match(error.message, /fallback init failed/);
    return true;
  });
  assert.equal(client.state.kind, 'failed');
  assert.equal(port.terminated, true);
  // A send after the failure is refused at once, not queued behind it.
  await assert.rejects(client.calculate(), { code: 'engine_init_failed' });
  assert.deepEqual(client.log.map((entry) => [entry.command, entry.ok, entry.code]), [
    ['snapshot', false, 'engine_init_failed'],
    ['calculate', false, 'engine_init_failed'],
  ]);
});

test('a worker reply to a request queued before init failed is a typed error, not silence', async () => {
  const posted = [];
  const host = createWorkerHost({ engine: Promise.reject(new Error('boom')), post: (m) => posted.push(m) });
  await host.receive({ id: 7, command: 'snapshot', args: {} });
  await host.receive({ id: 8, command: 'calculate', args: {} });
  assert.deepEqual(posted.filter((m) => m.type === 'init_failed').length, 1);
  const replies = posted.filter((m) => !('type' in m));
  assert.deepEqual(replies.map((m) => [m.id, m.command, m.result.ok, m.result.error.code]), [
    [7, 'snapshot', false, 'engine_init_failed'],
    [8, 'calculate', false, 'engine_init_failed'],
  ]);
  assert.equal(host.state.kind, 'failed');
});

test('one command throwing does not poison the queue; the next command still runs', async () => {
  let calls = 0;
  const engine = fakeEngine((request) => {
    calls += 1;
    if (request.command === 'calculate') throw new Error('engine panicked');
    return { revision: String(calls), sheets: [] };
  });
  const { port } = channel(Promise.resolve(engine));
  const client = new ForgeClient(port);
  const first = client.snapshot();
  const failing = client.calculate();
  const after = client.snapshot();
  assert.equal((await first).ok, true);
  const failed = await failing;
  assert.equal(failed.ok, false);
  assert.equal(failed.error.code, 'internal_error');
  assert.match(failed.error.message, /engine panicked/);
  const next = await after;
  assert.deepEqual(next, { ok: true, value: { revision: '3', sheets: [] } });
  assert.equal(client.state.kind, 'ready');
});

test('a reply that cannot be posted still answers the request with an error', async () => {
  const posted = [];
  let failNext = true;
  const host = createWorkerHost({
    engine: Promise.resolve(fakeEngine(() => ({ revision: '0', sheets: [] }))),
    post: (message) => {
      if (failNext && !('type' in message)) {
        failNext = false;
        throw new Error('DataCloneError');
      }
      posted.push(message);
    },
  });
  await host.receive({ id: 1, command: 'snapshot', args: {} });
  await host.receive({ id: 2, command: 'snapshot', args: {} });
  const replies = posted.filter((m) => !('type' in m));
  assert.deepEqual(replies.map((m) => [m.id, m.result.ok, m.result.ok ? null : m.result.error.code]), [
    [1, false, 'internal_error'],
    [2, true, null],
  ]);
});

test('a worker error event rejects every pending request and fails the client', async () => {
  const { port } = channel(neverSettles());
  const client = new ForgeClient(port);
  const a = client.snapshot();
  const b = client.calculate();
  port.dispatchEvent(Object.assign(new Event('error'), { message: 'Uncaught RangeError: out of memory' }));
  for (const pending of [a, b]) {
    await assert.rejects(pending, (error) => {
      assert.ok(error instanceof ForgeClientError);
      assert.equal(error.code, 'worker_error');
      assert.match(error.message, /out of memory/);
      return true;
    });
  }
  assert.equal(client.state.kind, 'failed');
  assert.equal(port.terminated, true);
  await assert.rejects(client.snapshot(), { code: 'worker_error' });
});

test('a messageerror event rejects pending requests', async () => {
  const { port } = channel(neverSettles());
  const client = new ForgeClient(port);
  const pending = client.snapshot();
  port.dispatchEvent(new Event('messageerror'));
  await assert.rejects(pending, { code: 'worker_error' });
  assert.equal(client.state.kind, 'failed');
  assert.equal(port.terminated, true);
});

test('close rejects pending requests, terminates the port and refuses new sends', async () => {
  const { port } = channel(neverSettles());
  const client = new ForgeClient(port);
  const pending = client.snapshot();
  client.close();
  await assert.rejects(pending, (error) => {
    assert.ok(error instanceof ForgeClientError);
    assert.equal(error.code, 'client_closed');
    return true;
  });
  assert.equal(client.state.kind, 'closed');
  assert.equal(port.terminated, true);
  await assert.rejects(client.snapshot(), { code: 'client_closed' });
  // Closing twice is harmless, and a later worker error does not reopen it.
  client.close();
  port.dispatchEvent(new Event('error'));
  assert.equal(client.state.kind, 'closed');
});

test('an optional request timeout rejects a request the worker never answers', async () => {
  const { port } = channel(neverSettles());
  const client = new ForgeClient(port, { requestTimeoutMs: 20 });
  await assert.rejects(client.snapshot(), { code: 'timeout' });
  // A timeout fails one request, not the client.
  assert.equal(client.state.kind, 'initializing');
});

test('the client becomes ready on the worker ready message and keeps routing patches', async () => {
  const patch = { type: 'patch', command_id: 'c1', client_id: 'x', actor: { kind: 'human', id: 'u', on_behalf_of: null },
    revision: '1', previous_revision: '0', changed: [], evaluated_cells: 0 };
  const engine = fakeEngine(() => ({ revision: '0', sheets: [] }));
  let drained = false;
  engine.drainPatches = () => (drained ? [] : ((drained = true), [patch]));
  const { port } = channel(Promise.resolve(engine));
  const client = new ForgeClient(port);
  const patches = [];
  client.onPatch((p) => patches.push(p));
  assert.equal(client.state.kind, 'initializing');
  assert.equal((await client.snapshot()).ok, true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(client.state.kind, 'ready');
  assert.deepEqual(patches, [patch]);
});
