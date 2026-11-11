// Web Worker entry: the only place the Forge WASM instance lives. The worker
// answers requests in arrival order; the WASM module resolves its .wasm file
// relative to this module (new URL(..., import.meta.url)), so bundlers and
// plain ES-module hosts both work. No network calls beyond loading that file.
// The .wasm bytes are fetched here (rather than by the glue) so their sha256
// can be recorded in the event log's session.opened engine info.

import init, { createBlankWorkbook, openXlsx, replayJson, sha256Hex } from '../pkg/workbook_forge.js';
import { ForgeEngine } from './engine';
import { createWorkerHost } from './worker-host';
import type { RequestMessage } from './protocol';

const wasm = { openXlsx, createBlankWorkbook, sha256Hex, replayJson };
// NOW and TODAY follow the user's zone; the engine resolves its offset per command.
const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
// Two attempts: fetch the bytes ourselves (to hash them), else let the glue
// load the file. If both fail the host enters its failed state and answers
// every request with engine_init_failed instead of leaving clients waiting.
const engine = (async () => {
  try {
    const bytes = new Uint8Array(await (await fetch(new URL('../pkg/workbook_forge_bg.wasm', import.meta.url))).arrayBuffer());
    await init({ module_or_path: bytes });
    return new ForgeEngine(wasm, { wasmSha256: sha256Hex(bytes), timeZone });
  } catch {
    await init();
    return new ForgeEngine(wasm, { wasmSha256: null, timeZone });
  }
})();
const worker = self as unknown as Worker;
const host = createWorkerHost({
  engine,
  post: (message, transfer = []) => worker.postMessage(message, transfer),
});

self.addEventListener('message', (event: MessageEvent<RequestMessage>) => {
  void host.receive(event.data);
});
