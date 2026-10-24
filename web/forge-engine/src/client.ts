// Typed client for the Forge command API over any MessagePort-like channel
// (a Worker, a MessagePort, or a WebView bridge with the same shape).

import type { Edit, CommandName, Commands, RequestMessage, ResponseMessage, Result, Revision } from './protocol';

export interface Port {
  postMessage(message: unknown, transfer?: Transferable[]): void;
  addEventListener(type: 'message', listener: (event: MessageEvent) => void): void;
}

export interface CommandLogEntry {
  command: CommandName;
  ok: boolean;
  code?: string;
}

export class ForgeClient {
  private nextId = 1;
  private pending = new Map<number, (response: ResponseMessage) => void>();
  /** Every command sent and how it ended; hosts may audit it. */
  readonly log: CommandLogEntry[] = [];

  private readonly port: Port;

  constructor(port: Port) {
    this.port = port;
    port.addEventListener('message', (event: MessageEvent<ResponseMessage>) => {
      const resolve = this.pending.get(event.data.id);
      if (resolve) {
        this.pending.delete(event.data.id);
        resolve(event.data);
      }
    });
  }

  private send<K extends CommandName>(command: K, args: Commands[K]['args'], transfer: Transferable[] = []) {
    const id = this.nextId++;
    const request: RequestMessage<K> = { id, command, args };
    return new Promise<Result<Commands[K]['result']>>((resolve) => {
      this.pending.set(id, (response) => {
        const result = response.result as Result<Commands[K]['result']>;
        this.log.push({ command, ok: result.ok, code: result.ok ? undefined : result.error.code });
        resolve(result);
      });
      this.port.postMessage(request, transfer);
    });
  }

  /** Open .xlsx bytes. The bytes are copied, so the caller keeps its buffer. */
  open(bytes: Uint8Array) {
    const copy = bytes.slice();
    return this.send('open', { bytes: copy }, [copy.buffer]);
  }
  snapshot() {
    return this.send('snapshot', {});
  }
  applyEdits(edits: Edit[], expectedRevision: Revision) {
    return this.send('applyEdits', { edits, expectedRevision });
  }
  calculate() {
    return this.send('calculate', {});
  }
  export() {
    return this.send('export', {});
  }
}

/** Start the engine in a dedicated module worker and return a client for it. */
export function startForgeWorker(): { client: ForgeClient; worker: Worker } {
  const worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module', name: 'forge-engine' });
  return { client: new ForgeClient(worker), worker };
}
