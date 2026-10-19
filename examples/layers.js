// boarddd layer view demo: CM5 MINIMA's copper from its plain Gerbers (boarddd.connectivity, server side), its
// KiCad stackup; boarddd/impedance/ui's layerView does the rest.
import { layerView } from 'boarddd/impedance/ui/index.js';

const gz = async (url) => JSON.parse(await new Response((await fetch(url)).body.pipeThrough(new DecompressionStream('gzip'))).text());
const data = await gz('../fixtures/connectivity/cm5-plain.json.gz');
const netInfo = Object.fromEntries(data.connectivity.nets.map((n) => [n.name, { auto: n.auto, pads: n.pads }]));
const out = document.getElementById('out');
const view = layerView(document.getElementById('view'), {
  board: data.board, copper: data.copper, netInfo, route: { worker: new URL('../src/impedance/ui/route-worker.js', import.meta.url) },
  rename: async (net, name) => { out.textContent = `renamed ${net} → ${name}`; },
});
view.on('control', (d) => { out.textContent = `control: ${JSON.stringify(d, null, 1)}`; });
window.demo = { view, data };
