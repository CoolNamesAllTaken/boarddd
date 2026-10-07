// The fork renderer from the dev copy in /vendor, injected the way a consumer would.
export async function gerberApi() {
  const base = '/vendor/wasm-gerber-renderer/';
  const mods = await Promise.all(['index.js', 'board.js', 'diff.js', 'drills.js', 'layers.js', 'outline.js', 'raster.js'].map((m) => import(base + m)));
  const glue = await import(base + 'wasm/wasm_gerber_processor.js');
  const api = Object.assign({}, ...mods);
  const renderer = await api.createGerberRenderer(document.createElement('canvas'), {
    wasmModule: glue, wasmInitInput: { module_or_path: new URL(base + 'wasm/wasm_gerber_processor_bg.wasm', location.href) },
    contextAttributes: { preserveDrawingBuffer: true },
  });
  return { api, renderer };
}
