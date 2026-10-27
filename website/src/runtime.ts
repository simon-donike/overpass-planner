import type { PositionsRequest, PositionsResult, PredictionRequest, Progress, Result, TracksResult, WorkerEvent } from './types';

/** One Python runtime, shared by predictions and the optional live view. */
export class BrowserRuntime {
  private worker?: Worker;
  private sequence = 0;
  private pending = new Map<number, {resolve: (result: any) => void; reject: (error: Error) => void; progress?: (event: Progress) => void}>();
  constructor(private base: string, private ready: (version: string) => void) {}

  predict(request: PredictionRequest, progress: (event: Progress) => void): Promise<Result> {
    return this.send('predict', request, progress);
  }
  positions(request: PositionsRequest, progress: (event: Progress) => void): Promise<PositionsResult> {
    return this.send('positions', request, progress);
  }
  tracks(request: PositionsRequest, progress: (event: Progress) => void): Promise<TracksResult> {
    return this.send('tracks', request, progress);
  }
  terminate() {
    this.worker?.terminate(); this.worker = undefined;
    for (const job of this.pending.values()) job.reject(new DOMException('Calculation cancelled', 'AbortError'));
    this.pending.clear();
  }
  private send<T>(type: string, request: PredictionRequest | PositionsRequest, progress: (event: Progress) => void): Promise<T> {
    if (!this.worker) {
      const current = new Worker(new URL('./worker.ts', import.meta.url), {type: 'module'});
      this.worker = current;
      current.onmessage = (event: MessageEvent<WorkerEvent>) => {
        if (this.worker !== current) return;
        const message = event.data, job = this.pending.get(message.id);
        if (!job) return;
        if (message.type === 'ready') this.ready(message.version);
        else if (message.type === 'progress') job.progress?.(message);
        else {
          this.pending.delete(message.id);
          if (message.type === 'error') {
            job.reject(new Error(message.message));
            if (message.fatal) this.terminate();
          }
          else job.resolve(message.result);
        }
      };
      current.onerror = event => {
        if (current !== this.worker) return;
        for (const job of this.pending.values()) job.reject(new Error(event.message || 'The browser worker stopped. Retry, or reduce the region/window if device memory is limited.'));
        this.pending.clear(); this.terminate();
      };
    }
    const id = ++this.sequence;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, {resolve, reject, progress});
      this.worker!.postMessage({type, id, request, base: this.base});
    });
  }
}
