// View state strings of boarddd/view2d (ported from kipr's viewstate tests).
import test from 'node:test';
import assert from 'node:assert/strict';
import { formatRegion, parseRegion, sameRegion, formatSlider, parseSlider, formatViewState, parseViewState } from '../../src/view2d/viewstate.js';

test('region round trips', () => {
  for (const r of [{ cx: 124.76, cy: 88.68, w: 7.027 }, { cx: -10.5, cy: 0, w: 300 }, { cx: 1.2345, cy: 2.3456, w: 0.5 }]) {
    assert.ok(sameRegion(parseRegion(formatRegion(r)), r), JSON.stringify(r));
  }
  assert.equal(formatRegion(null), null);
  assert.equal(formatRegion({ cx: 1, cy: 2, w: 0 }), null);
  assert.equal(formatRegion({ cx: NaN, cy: 2, w: 3 }), null);
  for (const bad of ['', '1,2', '1,2,3,4', 'a,b,c', '1,2,-3', '1,2,0', '1,2,1e9', undefined, 5]) assert.equal(parseRegion(bad), null, String(bad));
  assert.ok(sameRegion(null, null));
  assert.ok(!sameRegion(null, { cx: 0, cy: 0, w: 1 }));
  assert.ok(!sameRegion({ cx: 0, cy: 0, w: 10 }, { cx: 0.5, cy: 0, w: 10 }));
});

test('sliders', () => {
  assert.equal(formatSlider(0.5), '0.5');
  assert.equal(formatSlider(0.3, false), null);
  assert.equal(formatSlider(0.12345), '0.123');
  assert.equal(formatSlider(1.5), '1');
  assert.equal(parseSlider('0'), 0);
  for (const bad of [undefined, '', 'x', '-0.1', '1.1']) assert.equal(parseSlider(bad), null, String(bad));
  assert.equal(parseSlider(formatSlider(0.7)), 0.7);
});

test('whole state round trips through params and URLSearchParams; only the slider on show', () => {
  const s = { region: { cx: 150.5, cy: -97.25, w: 20 }, mode: 'swipe', swipe: 0.3, opacity: 0.8 };
  const p = formatViewState(s);
  assert.deepEqual(p, { z: '150.5,-97.25,20', mode: 'swipe', sw: '0.3' });
  const back = parseViewState(new URLSearchParams(p));
  assert.ok(sameRegion(back.region, s.region));
  assert.equal(back.mode, 'swipe');
  assert.equal(back.swipe, 0.3);
  assert.equal(back.opacity, undefined);
  assert.deepEqual(formatViewState({ mode: 'onion', opacity: 0.25, region: null }), { mode: 'onion', op: '0.25' });
  assert.deepEqual(parseViewState({ mode: 'side<script>' }), { region: null });
  assert.deepEqual(parseViewState(null), { region: null });
});
