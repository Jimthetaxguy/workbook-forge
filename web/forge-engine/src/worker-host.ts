// The worker's request loop, separate from the Worker global and the WASM
// import so its failure paths can be tested in Node. worker.ts wires it to
// `self` and to the real engine.
//
// Lifecycle (one explicit state, not a promise to re-await):
//   initializing --engine ready--> ready
//   initializing --engine failed--> failed (posts one init_failed message)
// Requests are answered strictly in arrival order. Every request gets exactly
// one response: the engine's, or a typed error. A step that throws is caught
// inside its own queue link, so one failure never poisons later commands.

import type {
  CommandName,
  EngineError,
  EventMessage,
  PatchEvent,
  RequestMessage,
  ResponseMessage,
  WorkerInitFailedMessage,
  WorkerReadyMessage,
} from './protocol';

/** The part of ForgeEngine the request loop drives. */
export interface WorkerEngine {
  handle(request: RequestMessage): ResponseMessage;
  drainPatches(): PatchEvent[];
  drainEvents(): EventMessage[];
}

export type WorkerHostState =
  | { kind: 'initializing' }
  | { kind: 'ready'; engine: WorkerEngine }
  | { kind: 'failed'; error: EngineError };

export type WorkerOutbound = ResponseMessage | PatchEvent | EventMessage | WorkerReadyMessage | WorkerInitFailedMessage;

export interface WorkerHostOptions {
  /** Resolves to the engine, or rejects when every initialisation attempt failed. */
  engine: Promise<WorkerEngine>;
  /** Posts to the submitting client (today the only client). */
  post(message: WorkerOutbound, transfer?: Transferable[]): void;
  /** Posts patches and log events; defaults to `post`. */
  broadcast?(message: PatchEvent | EventMessage): void;
}

export interface WorkerHost {
  readonly state: WorkerHostState;
  /** Queue one request; the promise settles once it has been answered. Never rejects. */
  receive(request: RequestMessage): Promise<void>;
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function errorResponse(request: RequestMessage, error: EngineError): ResponseMessage {
  return { id: request.id, command: request.command as CommandName, result: { ok: false, error } };
}

export function createWorkerHost(options: WorkerHostOptions): WorkerHost {
  const { post } = options;
  const broadcast = options.broadcast ?? ((message: PatchEvent | EventMessage) => post(message));
  let state: WorkerHostState = { kind: 'initializing' };

  const settled: Promise<void> = options.engine.then(
    (engine) => {
      state = { kind: 'ready', engine };
      try {
        post({ type: 'ready' });
      } catch {
        // The ready notice is advisory; responses still flow.
      }
    },
    (error: unknown) => {
      const failure: EngineError = { code: 'engine_init_failed', message: `Forge engine failed to start: ${describe(error)}` };
      state = { kind: 'failed', error: failure };
      try {
        post({ type: 'init_failed', error: failure });
      } catch {
        // Each queued request still gets its own engine_init_failed reply.
      }
    },
  );
  let queue: Promise<void> = settled;

  /** Answer one request; any throw here is the step's own failure. */
  function reply(request: RequestMessage, response: ResponseMessage, transfer: Transferable[] = []) {
    try {
      post(response, transfer);
    } catch (error) {
      // For example a DataCloneError: the client must still hear back.
      post(errorResponse(request, { code: 'internal_error', message: `Forge could not send the response: ${describe(error)}` }));
    }
  }

  function step(request: RequestMessage) {
    if (state.kind === 'failed') {
      reply(request, errorResponse(request, state.error));
      return;
    }
    if (state.kind !== 'ready') throw new Error('worker host stepped before the engine settled');
    const engine = state.engine;
    let response: ResponseMessage;
    try {
      response = engine.handle(request);
    } catch (error) {
      reply(request, errorResponse(request, { code: 'internal_error', message: `Forge engine failed on ${request.command}: ${describe(error)}` }));
      return;
    }
    const transfer: Transferable[] = [];
    if (response.result.ok && request.command === 'export') {
      transfer.push((response.result.value as { bytes: Uint8Array }).bytes.buffer as ArrayBuffer);
    }
    reply(request, response, transfer);
    // Ack before patch: the submitter's response goes first, then each patch
    // to every subscriber, then log events for event subscribers.
    // The request is already answered, so a failure here must not produce a
    // second response; it is reported on the worker console instead.
    try {
      for (const patch of engine.drainPatches()) broadcast(patch);
      for (const message of engine.drainEvents()) broadcast(message);
    } catch (error) {
      console.error(`Forge could not deliver patches after ${request.command}:`, error);
    }
  }

  return {
    get state() {
      return state;
    },
    receive(request) {
      const run = queue.then(() => {
        try {
          step(request);
        } catch (error) {
          // Last resort: reply or patch delivery itself threw. Answer with an
          // error and keep the queue alive; a second throw is dropped here.
          try {
            post(errorResponse(request, { code: 'internal_error', message: describe(error) }));
          } catch {
            // Nothing more can reach the client from this step.
          }
        }
      });
      queue = run;
      return run;
    },
  };
}
