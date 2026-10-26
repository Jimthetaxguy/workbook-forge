// Web Worker entry: the only place the Forge WASM instance lives. The worker
// answers requests in arrival order; the WASM module resolves its .wasm file
// relative to this module (new URL(..., import.meta.url)), so bundlers and
// plain ES-module hosts both work. No network calls beyond loading that file.
// The .wasm bytes are fetched here (rather than by the glue) so their sha256
// can be recorded in the event log's session.opened engine info.

import init, { createBlankWorkbook, openXlsx, replayJson, sha256Hex } from '../pkg/workbook_forge.js';
import { ForgeEngine } from './engine';
import type { RequestMessage } from './protocol';

const wasm = { openXlsx, createBlankWorkbook, sha256Hex, replayJson };
const ready = (async () => {
  try {
    const bytes = new Uint8Array(await (await fetch(new URL('../pkg/workbook_forge_bg.wasm', import.meta.url))).arrayBuffer());
    await init({ module_or_path: bytes });
    return new ForgeEngine(wasm, { wasmSha256: sha256Hex(bytes) });
  } catch {
    await init();
    return new ForgeEngine(wasm, { wasmSha256: null });
  }
})();
let queue: Promise<unknown> = Promise.resolve();
/** Every client that receives patches. One today (the page that started us). */
const subscribers: { postMessage(message: unknown): void }[] = [self as unknown as Worker];

self.addEventListener('message', (event: MessageEvent<RequestMessage>) => {
  const request = event.data;
  queue = queue.then(async () => {
    const engine = await ready;
    const response = engine.handle(request);
    const transfer: Transferable[] = [];
    if (response.result.ok && request.command === 'export') {
      transfer.push((response.result.value as { bytes: Uint8Array }).bytes.buffer as ArrayBuffer);
    }
    (self as unknown as Worker).postMessage(response, transfer);
    // Ack before patch: the submitter's response goes first, then each patch
    // to every subscriber, then log events for event subscribers.
    for (const patch of engine.drainPatches()) for (const port of subscribers) port.postMessage(patch);
    for (const message of engine.drainEvents()) for (const port of subscribers) port.postMessage(message);
  });
});
