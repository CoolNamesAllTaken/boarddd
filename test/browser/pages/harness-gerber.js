// boarddd/gerber injected the way a caller with its own renderer would (diff.html uses the defaults instead).
export async function gerberApi() {
  const api = await import('/src/gerber/index.js');
  const renderer = await api.createGerberRenderer(document.createElement('canvas'), { contextAttributes: { preserveDrawingBuffer: true } });
  return { api, renderer };
}
