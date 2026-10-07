// view2d under style-src 'self': no injected <style>; STAGE_CSS comes from view2d-csp.css (a copy).
import * as view2d from '/src/view2d/index.js';
import * as gerber from '/src/gerber/index.js';

const violations = [];
document.addEventListener('securitypolicyviolation', (e) => violations.push(`${e.violatedDirective} ${e.blockedURI}`));
window.run = async (injectCss) => {
  const npth = await (await fetch('/test/fixtures/pic_programmer/base/pic_programmer-NPTH.drl')).text();
  const renderer = await gerber.createGerberRenderer(document.createElement('canvas'));
  const stage = view2d.createStage(document.getElementById('host'), { renderer, injectCss, bounds: { minX: 70, maxX: 236, minY: -142, maxY: -38 } });
  const busy = [];
  stage.on('busy', (e) => busy.push(e.busy));
  stage.setScene([{ className: 'mine', side: 'head', layers: [{ content: view2d.layers([{ source: npth, kind: 'drill', color: [1, 0, 0] }]) }] }]);
  await stage.ready();
  const pane = document.querySelector('.bd2-pane');
  await new Promise((r) => setTimeout(r, 400));
  await stage.ready();
  const before = busy.length;
  const v = stage.getView();
  stage.setView({ ...v, cx: v.cx + 3 }); // a pan: nothing to re-render, never busy
  await new Promise((r) => setTimeout(r, 400));
  await stage.ready();
  const panBusy = busy.length - before;
  stage.setView({ ...v, s: v.s * 4 }); // a zoom: busy, then idle
  await stage.ready();
  return {
    violations, busy, before, panBusy, renders: stage.stats().renders,
    styles: document.querySelectorAll('.bd2-stage style').length,
    position: getComputedStyle(pane).position,
    cls: pane.className, side: pane.dataset.side,
    css: view2d.STAGE_CSS.includes('.bd2-pane'),
  };
};
window.ready = true;
