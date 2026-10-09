// boarddd/impedance/ui: the components' CSS. Every colour and size is a variable a host overrides on .bdi-panel,
// .bd2-stage or any ancestor (e.g. :root); light by default, dark under prefers-color-scheme or [data-theme=dark].
export const IMPEDANCE_UI_CSS = `
:where(.bdi-panel, .bd2-stage) {
  --bdi-bg: #fcfcfb; --bdi-fg: #0b0b0b; --bdi-muted: #6b6a66; --bdi-line: #e4e3df; --bdi-chip: #efeee9;
  --bdi-ok: #1a8f5a; --bdi-warn: #c98500; --bdi-bad: #d0402b; --bdi-accent: #2a78d6; --bdi-band: rgba(26, 143, 90, 0.10);
  --bdi-cu: #c8803a; --bdi-plane: #9a6630; --bdi-diel: #e9ddb8; --bdi-mask: #6da86f; --bdi-dim: #000;
  --bdi-hl: #ffd23f; --bdi-hl-section: #2a78d6; --bdi-marker: #ffffff; --bdi-font: 12px/1.3 system-ui, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :where(.bdi-panel, .bd2-stage):where(:not([data-theme=light] *)) {
    --bdi-bg: #1a1a19; --bdi-fg: #f2f1ec; --bdi-muted: #a3a29a; --bdi-line: #34342f; --bdi-chip: #2a2a27;
    --bdi-band: rgba(46, 178, 112, 0.14); --bdi-diel: #5b5133; --bdi-mask: #3f7a42;
  }
}
:where([data-theme=dark]) :where(.bdi-panel, .bd2-stage) {
  --bdi-bg: #1a1a19; --bdi-fg: #f2f1ec; --bdi-muted: #a3a29a; --bdi-line: #34342f; --bdi-chip: #2a2a27;
  --bdi-band: rgba(46, 178, 112, 0.14); --bdi-diel: #5b5133; --bdi-mask: #3f7a42;
}
.bdi-panel { background: var(--bdi-bg); color: var(--bdi-fg); font: var(--bdi-font); padding: 8px; display: flex; flex-direction: column; gap: 6px; min-width: 260px; box-sizing: border-box; }
.bdi-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 10px; }
.bdi-nets { color: var(--bdi-muted); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bdi-z { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
.bdi-verdict[data-v=ok] { color: var(--bdi-ok); } .bdi-verdict[data-v=warn] { color: var(--bdi-warn); } .bdi-verdict[data-v=bad] { color: var(--bdi-bad); }
.bdi-override { font: inherit; background: var(--bdi-chip); color: inherit; border: 1px solid var(--bdi-line); border-radius: 4px; padding: 1px 4px; }
.bdi-chip { background: var(--bdi-chip); border-radius: 4px; padding: 1px 5px; margin-right: 3px; color: var(--bdi-muted); }
.bdi-progress { height: 2px; background: var(--bdi-line); visibility: hidden; }
.bdi-panel[data-state=busy] .bdi-progress { visibility: visible; }
.bdi-bar { height: 100%; width: 0; background: var(--bdi-accent); transition: width 0.15s; }
.bdi-panel[data-state=empty] :is(.bdi-head, .bdi-profile, .bdi-xsbox, .bdi-disc, .bdi-loss, .bdi-fchart) { display: none; }
.bdi-panel:not([data-loss=on]) :is(.bdi-loss, .bdi-fchart) { display: none; }
.bdi-loss { display: flex; align-items: baseline; gap: 8px; }
.bdi-il { font-size: 15px; font-weight: 600; font-variant-numeric: tabular-nums; cursor: help; }
.bdi-freq { font: inherit; background: var(--bdi-chip); color: inherit; border: 1px solid var(--bdi-line); border-radius: 4px; padding: 1px 4px; }
.bdi-fchart { height: 72px; }
.bdi-f0 { stroke: var(--bdi-line); stroke-width: 1; }
.bdi-fdb { fill: none; stroke: var(--bdi-accent); stroke-width: 2; stroke-linejoin: round; }
.bdi-fz { fill: none; stroke: var(--bdi-muted); stroke-width: 2; stroke-linejoin: round; }
.bdi-fmark { stroke: var(--bdi-fg); stroke-width: 1; opacity: 0.35; }
.bdi-fdbt { fill: var(--bdi-accent); font: var(--bdi-font); font-size: 11px; font-variant-numeric: tabular-nums; }
.bdi-fzt { fill: var(--bdi-muted); font: var(--bdi-font); font-size: 11px; font-variant-numeric: tabular-nums; }
.bdi-fax { fill: var(--bdi-muted); font: var(--bdi-font); font-size: 9px; opacity: 0.8; }
.bdi-panel:not([data-state=empty]) .bdi-empty { display: none; }
.bdi-empty { color: var(--bdi-muted); font-size: 22px; text-align: center; padding: 12px; cursor: help; }
.bdi-profile { height: 64px; }
.bdi-chart { display: block; width: 100%; height: 100%; }
.bdi-band { fill: var(--bdi-band); } .bdi-target { stroke: var(--bdi-muted); stroke-dasharray: 3 3; stroke-width: 1; }
.bdi-seg { stroke-width: 3; stroke-linecap: round; } .bdi-ok { stroke: var(--bdi-ok); } .bdi-warn { stroke: var(--bdi-warn); } .bdi-bad { stroke: var(--bdi-bad); }
.bdi-noz { fill: var(--bdi-muted); } .bdi-via { stroke: var(--bdi-muted); stroke-width: 1; opacity: 0.6; }
.bdi-cursor { stroke: var(--bdi-accent); stroke-width: 1; } .bdi-hit { fill: transparent; cursor: crosshair; }
.bdi-xsbox { height: 150px; }
.bdi-xs { display: block; width: 100%; height: 100%; }
.bdi-xs-diel { fill: var(--bdi-diel); } .bdi-xs-mask { fill: var(--bdi-mask); opacity: 0.8; }
.bdi-xs-cu { fill: var(--bdi-cu); } .bdi-xs-plane { fill: var(--bdi-plane); } .bdi-xs-gnd { fill: var(--bdi-plane); }
.bdi-xs-er { fill: var(--bdi-muted); }
.bdi-xs-dims { visibility: hidden; } .bdi-xs:hover .bdi-xs-dims { visibility: visible; }
.bdi-xs-dim { stroke: var(--bdi-accent); stroke-width: 0.004; vector-effect: non-scaling-stroke; } .bdi-xs-dimtext { fill: var(--bdi-accent); }
.bdi-xs-none { color: var(--bdi-bad); text-align: center; font-size: 22px; padding: 30px; cursor: help; }
.bdi-disc { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px; }
.bdi-d { background: var(--bdi-chip); border-radius: 4px; padding: 1px 6px; cursor: pointer; font-variant-numeric: tabular-nums; }
.bdi-d:hover, .bdi-d:focus { outline: 1px solid var(--bdi-accent); }
.bdi-sym { margin-right: 3px; } .bdi-s { color: var(--bdi-muted); }
.bdi-d-no_ref .bdi-sym, .bdi-d-plane_gap .bdi-sym { color: var(--bdi-bad); } .bdi-d-ref_edge .bdi-sym, .bdi-d-uncoupled .bdi-sym { color: var(--bdi-warn); }
.bdi-hl .bdi-dim { fill: var(--bdi-dim); }
.bd2-world .bdi-hl :is(.bdi-hl-track, .bdi-hl-section) { vector-effect: none; } /* widths in mm (view2d strokes are px) */
.bdi-hl .bdi-hl-track { fill: none; stroke: var(--bdi-hl); stroke-linecap: round; stroke-linejoin: round; }
.bdi-hl .bdi-hl-pad, .bdi-hl .bdi-hl-via { fill: var(--bdi-hl); } .bdi-hl .bdi-hl-zone { fill: var(--bdi-hl); opacity: 0.5; }
.bdi-hl .bdi-hl-section { fill: none; stroke: var(--bdi-hl-section); stroke-linecap: round; opacity: 0.85; }
.bdi-hl .bdi-marker { fill: none; stroke: var(--bdi-marker); } .bdi-hl .bdi-marker-halo { fill: none; stroke: #000; opacity: 0.6; } .bdi-hl .bdi-ring { fill: none; stroke: var(--bdi-bad); }
.bdi-tip { position: absolute; z-index: 5; pointer-events: none; background: var(--bdi-bg); color: var(--bdi-fg); font: var(--bdi-font); padding: 2px 6px; border-radius: 4px; box-shadow: 0 1px 4px rgba(0,0,0,0.3); white-space: nowrap; }
`;
