// Web Worker entry: the only place the Forge WASM instance lives. The worker
// answers requests in arrival order; the WASM module resolves its .wasm file
// relative to this module (new URL(..., import.meta.url)), so bundlers and
// plain ES-module hosts both work. No network calls beyond loading that file.

import init, { createBlankWorkbook, openXlsx } from '../pkg/workbook_forge.js';
import { ForgeEngine } from './engine';
import type { RequestMessage } from './protocol';

const ready = init().then(() => new ForgeEngine({ openXlsx, createBlankWorkbook }));
let queue: Promise<unknown> = Promise.resolve();

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
  });
});
