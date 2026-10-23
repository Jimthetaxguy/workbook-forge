import type { Request, Transport } from './dist/client.js';
export interface ProcessOptions { cwd?: string; env?: Record<string, string>; timeoutMs?: number }
/** Host-selected command only; no shell, retry, reconnect or durable storage. */
export class JsonLineProcessTransport implements Transport {
  constructor(command: string, args?: string[], options?: ProcessOptions);
  request(request: Request): Promise<unknown>;
  close(): Promise<void>;
}
