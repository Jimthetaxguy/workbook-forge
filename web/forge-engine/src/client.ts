// Typed client for the Forge command API over any MessagePort-like channel
// (a Worker, a MessagePort, or a WebView bridge with the same shape).

import type {
  CommandEnvelope,
  CommandMeta,
  Edit,
  EventMessage,
  LogEvent,
  CommandName,
  Commands,
  PatchEvent,
  RequestMessage,
  ResponseMessage,
  Result,
  Revision,
  WorkerInitFailedMessage,
  WorkerReadyMessage,
} from './protocol';

/**
 * The channel to the engine: a Worker, a MessagePort, or a WebView bridge with
 * the same shape. `error` and `messageerror` events, where the channel has
 * them, fail the client; `terminate` or `close`, where present, is called by
 * ForgeClient.close().
 */
export interface Port {
  postMessage(message: unknown, transfer?: Transferable[]): void;
  addEventListener(type: 'message' | 'error' | 'messageerror', listener: (event: MessageEvent) => void): void;
  terminate?(): void;
  close?(): void;
}

export interface CommandLogEntry {
  command: CommandName;
  ok: boolean;
  code?: string;
}

/**
 * Why a request was rejected rather than answered. A command the engine
 * refuses still resolves with `{ ok: false }`; these are transport and
 * lifecycle failures where no engine answer will come.
 */
export type ForgeClientErrorCode = 'engine_init_failed' | 'worker_error' | 'client_closed' | 'timeout';

export class ForgeClientError extends Error {
  readonly code: ForgeClientErrorCode;
  constructor(code: ForgeClientErrorCode, message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = 'ForgeClientError';
    this.code = code;
  }
}

/**
 * The client's lifecycle. Sends are accepted while initializing (the worker
 * queues them) and while ready; failed and closed are terminal and reject
 * every pending and later send.
 */
export type ClientState =
  | { kind: 'initializing' }
  | { kind: 'ready' }
  | { kind: 'failed'; error: ForgeClientError }
  | { kind: 'closed' };

export interface ForgeClientOptions {
  /** Reject a request the worker has not answered after this many ms. Off by default. */
  requestTimeoutMs?: number;
}

interface PendingRequest {
  command: CommandName;
  resolve(response: ResponseMessage): void;
  reject(error: ForgeClientError): void;
}

type Inbound = ResponseMessage | PatchEvent | EventMessage | WorkerReadyMessage | WorkerInitFailedMessage;

export class ForgeClient {
  private nextId = 1;
  private pending = new Map<number, PendingRequest>();
  private patchListeners = new Set<(patch: PatchEvent) => void>();
  private eventListeners = new Set<(event: LogEvent) => void>();
  private current: ClientState = { kind: 'initializing' };
  /** Every command sent and how it ended; hosts may audit it. */
  readonly log: CommandLogEntry[] = [];

  private readonly port: Port;
  private readonly requestTimeoutMs: number | undefined;

  constructor(port: Port, options: ForgeClientOptions = {}) {
    this.port = port;
    this.requestTimeoutMs = options.requestTimeoutMs;
    port.addEventListener('message', (event: MessageEvent<Inbound>) => this.receive(event.data));
    port.addEventListener('error', (event) => {
      const detail = (event as unknown as ErrorEvent).message;
      this.fail(new ForgeClientError('worker_error', `Forge worker failed${detail ? `: ${detail}` : ''}`, { cause: event }));
    });
    port.addEventListener('messageerror', (event) => {
      this.fail(new ForgeClientError('worker_error', 'Forge worker sent a message that could not be read', { cause: event }));
    });
  }

  get state(): ClientState {
    return this.current;
  }

  /** Reject every pending request, stop the channel and refuse later sends. */
  close() {
    if (this.current.kind === 'closed') return;
    this.current = { kind: 'closed' };
    this.rejectAll(new ForgeClientError('client_closed', 'Forge client was closed'));
    if (this.port.terminate) this.port.terminate();
    else this.port.close?.();
  }

  private receive(data: Inbound) {
    if (this.current.kind === 'closed' || this.current.kind === 'failed') return;
    if ('type' in data) {
      if (data.type === 'patch') {
        for (const listener of this.patchListeners) listener(data);
      } else if (data.type === 'event') {
        for (const listener of this.eventListeners) listener(data.event);
      } else if (data.type === 'ready') {
        this.current = { kind: 'ready' };
      } else if (data.type === 'init_failed') {
        this.fail(new ForgeClientError('engine_init_failed', data.error.message, { cause: data.error }));
      }
      return;
    }
    const entry = this.pending.get(data.id);
    if (!entry) return;
    this.pending.delete(data.id);
    if (!data.result.ok && data.result.error.code === 'engine_init_failed') {
      // A worker that started failed before this client saw init_failed.
      this.fail(new ForgeClientError('engine_init_failed', data.result.error.message, { cause: data.result.error }));
      this.settleRejected(entry, this.failure());
      return;
    }
    if (this.current.kind === 'initializing') this.current = { kind: 'ready' };
    entry.resolve(data);
  }

  private failure(): ForgeClientError {
    if (this.current.kind === 'failed') return this.current.error;
    return new ForgeClientError('client_closed', 'Forge client was closed');
  }

  private fail(error: ForgeClientError) {
    if (this.current.kind === 'closed' || this.current.kind === 'failed') return;
    this.current = { kind: 'failed', error };
    this.rejectAll(error);
  }

  private rejectAll(error: ForgeClientError) {
    const entries = [...this.pending.values()];
    this.pending.clear();
    for (const entry of entries) this.settleRejected(entry, error);
  }

  private settleRejected(entry: PendingRequest, error: ForgeClientError) {
    this.log.push({ command: entry.command, ok: false, code: error.code });
    entry.reject(error);
  }

  private send<K extends CommandName>(command: K, args: Commands[K]['args'], transfer: Transferable[] = []) {
    return new Promise<Result<Commands[K]['result']>>((resolve, reject) => {
      if (this.current.kind === 'failed' || this.current.kind === 'closed') {
        this.settleRejected({ command, resolve: () => {}, reject }, this.failure());
        return;
      }
      const id = this.nextId++;
      const request: RequestMessage<K> = { id, command, args };
      let timer: ReturnType<typeof setTimeout> | undefined;
      const entry: PendingRequest = {
        command,
        resolve: (response) => {
          if (timer !== undefined) clearTimeout(timer);
          const result = response.result as Result<Commands[K]['result']>;
          this.log.push({ command, ok: result.ok, code: result.ok ? undefined : result.error.code });
          resolve(result);
        },
        reject: (error) => {
          if (timer !== undefined) clearTimeout(timer);
          reject(error);
        },
      };
      this.pending.set(id, entry);
      if (this.requestTimeoutMs !== undefined) {
        timer = setTimeout(() => {
          if (this.pending.get(id) !== entry) return;
          this.pending.delete(id);
          this.settleRejected(entry, new ForgeClientError('timeout', `Forge did not answer ${command} within ${this.requestTimeoutMs} ms`));
        }, this.requestTimeoutMs);
      }
      try {
        this.port.postMessage(request, transfer);
      } catch (error) {
        this.pending.delete(id);
        this.settleRejected(entry, new ForgeClientError('worker_error', `Forge request ${command} could not be sent`, { cause: error }));
      }
    });
  }

  /** Open .xlsx bytes. The bytes are copied, so the caller keeps its buffer. */
  open(bytes: Uint8Array, name?: string) {
    const copy = bytes.slice();
    return this.send('open', name === undefined ? { bytes: copy } : { bytes: copy, name }, [copy.buffer]);
  }
  newWorkbook(sheetName = 'Sheet1') {
    return this.send('newWorkbook', { sheetName });
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
  /** Export the Forge XLSX; with includeLog also the session log as NDJSON. */
  export(options: { includeLog?: boolean } = {}) {
    return this.send('export', options.includeLog ? { includeLog: true } : {});
  }
  /** Apply one command envelope and calculate; answers with the delta. Logged. */
  applyAndCalculate(envelope: CommandEnvelope, command?: CommandMeta) {
    return this.send('applyAndCalculate', command ? { envelope, command } : { envelope });
  }
  /** Read the session event log from fromSeq. */
  readLog(fromSeq = 0, limit = 1000) {
    return this.send('log', { fromSeq, limit });
  }
  /** Stream every new log event to onEvent listeners (events:true). */
  subscribe(options: { events: boolean }) {
    return this.send('subscribe', { events: options.events });
  }
  /** Replay a log onto its baseline bytes (sha256-checked); a mismatch is an error. */
  replay(baseline: Uint8Array, events: string | LogEvent[]) {
    const copy = baseline.slice();
    return this.send('replay', { baseline: copy, events }, [copy.buffer]);
  }
  /** Log events, after subscribe({events:true}). */
  onEvent(listener: (event: LogEvent) => void): () => void {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }
  /** Subscribe to patches the worker broadcasts after each applied command. */
  onPatch(listener: (patch: PatchEvent) => void): () => void {
    this.patchListeners.add(listener);
    return () => this.patchListeners.delete(listener);
  }
}

/** Start the engine in a dedicated module worker and return a client for it. */
export function startForgeWorker(): { client: ForgeClient; worker: Worker } {
  const worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module', name: 'forge-engine' });
  return { client: new ForgeClient(worker), worker };
}
