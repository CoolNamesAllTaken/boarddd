// boarddd/impedance/ui: a module worker that runs analyzeNet off the main thread (createAnalyzer starts it).
//   { type: 'load', board, copper }                  keep a board and its copper (sent once; the cache stays warm)
//   { type: 'analyze', id, nets, options }           → { id, progress } … then { id, result } or { id, error }
import { analyzeNet } from '../route.js';

let board = null;
let copper = null;
let cache = new Map();

self.onmessage = ({ data }) => {
  if (data.type === 'load') {
    ({ board, copper } = data);
    cache = new Map();
    return;
  }
  if (data.type !== 'analyze') return;
  const { id, nets, options } = data;
  try {
    let last = 0;
    const onProgress = (p) => {
      const now = performance.now();
      if (now - last > 50 || p.done === p.total) { last = now; self.postMessage({ id, progress: p }); }
    };
    const result = analyzeNet(board, copper, nets, { ...options, cache, onProgress });
    self.postMessage({ id, result });
  } catch (error) {
    self.postMessage({ id, error: error instanceof Error ? error.message : String(error) });
  }
};
