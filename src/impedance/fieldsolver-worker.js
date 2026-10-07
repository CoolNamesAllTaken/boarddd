// boarddd/impedance: a module worker that runs the tier-2 field solver off the main thread.
//
//     const worker = new Worker(new URL('boarddd/impedance/fieldsolver-worker.js', import.meta.url), { type: 'module' });
//     worker.postMessage({ id, section, opts });               // solveCrossSection(section, opts)
//     worker.postMessage({ id, model, params, opts });         // fieldCalculate(model, params, opts)
//     worker.onmessage = ({ data: { id, result, error } }) => { ... };
//
// fieldsolver.js itself has no DOM or Worker dependencies, so a page loaded from file:// (where module workers
// are unavailable) imports it and calls solveCrossSection on the main thread instead.
import { fieldCalculate, solveCrossSection } from './fieldsolver.js';

self.onmessage = (event) => {
  const { id, section, model, params, opts } = event.data;
  try {
    const result = model != null ? fieldCalculate(model, params, opts) : solveCrossSection(section, opts);
    self.postMessage({ id, result });
  } catch (error) {
    self.postMessage({ id, error: error instanceof Error ? error.message : String(error) });
  }
};
