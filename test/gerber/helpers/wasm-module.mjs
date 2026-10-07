// The committed wasm (third_party/wasm-gerber-renderer/core/wasm), initialised once for node tests.
import { readFileSync } from "node:fs";

const DIR = new URL("../../../third_party/wasm-gerber-renderer/core/wasm/", import.meta.url);
let modulePromise = null;

export function loadWasmModule() {
  modulePromise ||= (async () => {
    const wasmModule = await import(new URL("wasm_gerber_processor.js", DIR).href);
    await wasmModule.default({ module_or_path: readFileSync(new URL("wasm_gerber_processor_bg.wasm", DIR)) });
    return wasmModule;
  })();
  return modulePromise;
}
