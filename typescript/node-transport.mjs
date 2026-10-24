/** Optional Node adapter. Both backend commands use the existing JSON-lines runners. */
import { spawn } from 'node:child_process';
import { encodeRequest, LIMITS } from './dist/client.js';

export class JsonLineProcessTransport {
  constructor(command, args = [], options = {}) {
    if (typeof command !== 'string' || !command.length || !Array.isArray(args) || args.some(x => typeof x !== 'string')) throw new TypeError('Host must select a command and string arguments');
    const timeout = options.timeoutMs ?? 30000;
    if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 120000) throw new TypeError('timeoutMs must be 1..120000');
    this.timeoutMs = timeout;
    this.pending = null;
    this.stopped = false;
    this.buffer = Buffer.alloc(0);
    this.stderr = '';
    this.child = spawn(command, args, { cwd: options.cwd, env: options.env, stdio: ['pipe', 'pipe', 'pipe'], shell: false });
    this.child.stdout.on('data', chunk => this.receive(chunk));
    this.child.stderr.on('data', chunk => { this.stderr = (this.stderr + chunk.toString('utf8')).slice(-4096); });
    this.child.on('error', error => this.stop(error));
    this.child.on('exit', (code, signal) => this.stop(new Error(`Backend exited (${code ?? signal}); outcome may be uncertain`)));
    this.child.stdin.on('error', error => this.stop(error));
  }
  stop(error) {
    this.stopped = true;
    if (this.pending) { clearTimeout(this.pending.timer); const pending = this.pending; this.pending = null; pending.reject(error); }
    if (this.child.exitCode === null && this.child.signalCode === null) this.child.kill();
  }
  receive(chunk) {
    if (this.stopped) return;
    this.buffer = Buffer.concat([this.buffer, chunk]);
    const end = this.buffer.indexOf(10);
    if (end < 0 && this.buffer.byteLength > LIMITS.max_response_bytes || end > LIMITS.max_response_bytes) { this.stop(new Error('Backend response exceeds protocol limit')); return; }
    if (end < 0) return;
    if (!this.pending || end !== this.buffer.byteLength - 1) { this.stop(new Error('Unexpected or multiple backend responses')); return; }
    const line = this.buffer.subarray(0, end);
    this.buffer = Buffer.alloc(0);
    let response;
    try { response = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(line)); }
    catch { this.stop(new Error('Malformed backend JSON/UTF-8')); return; }
    const pending = this.pending; this.pending = null; clearTimeout(pending.timer); pending.resolve(response);
  }
  request(request) {
    if (this.stopped) return Promise.reject(new Error('Backend transport is closed'));
    if (this.pending) return Promise.reject(new Error('Process transport requires sequential requests'));
    let encoded;
    try { encoded = encodeRequest(request); } catch (error) { return Promise.reject(error); }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.stop(new Error('Backend timeout; no automatic retry; mutation/export may have completed')), this.timeoutMs);
      this.pending = { resolve, reject, timer };
      this.child.stdin.write(encoded + '\n');
    });
  }
  async close() {
    if (this.stopped) return;
    this.stop(new Error('Host closed the transient session'));
    // No persistence/reconnect contract. Host may inspect retained export files.
  }
}
