// boarddd/impedance/ui: analyzeNet behind a promise, in a module Worker (route-worker.js) when the page can start
// one, else on the main thread (file:// pages, CSPs without worker-src): the same result either way.
import { analyzeNet } from '../route.js';

/**
 * `createAnalyzer({ board, copper, worker })`: `analyze(nets, options, onProgress)` resolves to the
 * boarddd/impedance@1 document. `worker`: true (default: try), false (main thread), or a Worker URL.
 * A newer analyze() call supersedes an older one still running: the older promise rejects with 'superseded'.
 */
export function createAnalyzer({ board, copper, worker = true } = {}) {
  let w = null;
  if (worker !== false && typeof Worker !== 'undefined' && !(typeof location !== 'undefined' && location.protocol === 'file:')) {
    try {
      w = new Worker(worker === true ? new URL('./route-worker.js', import.meta.url) : worker, { type: 'module' });
      w.postMessage({ type: 'load', board, copper });
    } catch { w = null; }
  }
  const cache = new Map();
  const pending = new Map();
  let seq = 0;
  if (w) {
    w.onmessage = ({ data }) => {
      const job = pending.get(data.id);
      if (!job) return;
      if (data.progress) { job.onProgress?.(data.progress); return; }
      pending.delete(data.id);
      if (data.error) job.reject(new Error(data.error)); else job.resolve(data.result);
    };
    w.onerror = (e) => { for (const job of pending.values()) job.reject(new Error(e.message || 'worker error')); pending.clear(); };
  }
  return {
    /** 'worker' or 'main'. */
    get mode() { return w ? 'worker' : 'main'; },
    analyze(nets, options = {}, onProgress = null) {
      const id = ++seq;
      for (const [k, job] of pending) { job.reject(new Error('superseded')); pending.delete(k); }
      if (w) {
        return new Promise((resolve, reject) => {
          pending.set(id, { resolve, reject, onProgress });
          w.postMessage({ type: 'analyze', id, nets, options });
        });
      }
      // main thread: let the page paint the progress state first
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject, onProgress });
        setTimeout(() => {
          if (!pending.has(id)) return;
          try {
            const result = analyzeNet(board, copper, nets, { ...options, cache, onProgress });
            pending.delete(id);
            resolve(result);
          } catch (err) { pending.delete(id); reject(err); }
        }, 30);
      });
    },
    destroy() { w?.terminate(); w = null; pending.clear(); },
  };
}
