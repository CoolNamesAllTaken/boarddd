// boarddd/impedance/ui: the components' CSS. Every colour and size is a variable a host overrides on .bdi-panel,
// .bd2-stage or any ancestor (e.g. :root); light by default, dark under prefers-color-scheme or [data-theme=dark].
export const IMPEDANCE_UI_CSS = `
:where(.bdi-panel, .bd2-stage, .bdi-lv) {
  --bdi-bg: #fcfcfb; --bdi-fg: #0b0b0b; --bdi-muted: #6b6a66; --bdi-line: #e4e3df; --bdi-chip: #efeee9;
  --bdi-ok: #1a8f5a; --bdi-warn: #c98500; --bdi-bad: #d0402b; --bdi-accent: #2a78d6; --bdi-band: rgba(26, 143, 90, 0.10);
  --bdi-cu: #c8803a; --bdi-plane: #9a6630; --bdi-diel: #e9ddb8; --bdi-mask: #6da86f; --bdi-dim: #000;
  --bdi-hl: #ffd23f; --bdi-hl-section: #2a78d6; --bdi-marker: #ffffff; --bdi-font: 12px/1.3 system-ui, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :where(.bdi-panel, .bd2-stage, .bdi-lv):where(:not([data-theme=light] *)) {
    --bdi-bg: #1a1a19; --bdi-fg: #f2f1ec; --bdi-muted: #a3a29a; --bdi-line: #34342f; --bdi-chip: #2a2a27;
    --bdi-band: rgba(46, 178, 112, 0.14); --bdi-diel: #5b5133; --bdi-mask: #3f7a42;
  }
}
:where([data-theme=dark]) :where(.bdi-panel, .bd2-stage, .bdi-lv) {
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
.bdi-group { display: flex; flex-direction: column; gap: 4px; border-bottom: 1px solid var(--bdi-line); padding-bottom: 6px; }
.bdi-group-head { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; }
.bdi-gbtn { font: inherit; min-width: 26px; height: 24px; background: var(--bdi-chip); color: inherit; border: 1px solid var(--bdi-line); border-radius: 4px; padding: 0 6px; cursor: pointer; }
.bdi-gbtn:disabled { opacity: 0.4; cursor: default; }
.bdi-gbtn:not(:disabled):hover, .bdi-gbtn:focus-visible { outline: 1px solid var(--bdi-accent); }
.bdi-similar { font-size: 15px; }
.bdi-apply:not(:disabled) { background: var(--bdi-accent); border-color: var(--bdi-accent); color: #fff; }
.bdi-pattern { font: inherit; flex: 1 1 140px; min-width: 0; height: 24px; box-sizing: border-box; background: var(--bdi-bg); color: inherit; border: 1px solid var(--bdi-line); border-radius: 4px; padding: 0 6px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.bdi-pattern.is-bad { border-color: var(--bdi-bad); }
.bdi-gcount { font-weight: 600; font-variant-numeric: tabular-nums; cursor: help; }
.bdi-ggeom { color: var(--bdi-muted); font-variant-numeric: tabular-nums; cursor: help; }
.bdi-group[data-state=empty] :is(.bdi-apply, .bdi-gclear, .bdi-members) { display: none; }
.bdi-members { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px; max-height: 128px; overflow-y: auto; }
.bdi-member label { display: inline-flex; align-items: center; gap: 4px; background: var(--bdi-chip); border-radius: 4px; padding: 1px 6px 1px 3px; cursor: pointer; font-variant-numeric: tabular-nums; }
.bdi-member:not(.is-on) label { opacity: 0.45; text-decoration: line-through; }
.bdi-member input { margin: 0; accent-color: var(--bdi-accent); }
.bdi-msym { color: var(--bdi-muted); }
.bdi-hl .bdi-hl-other { opacity: 0.35; } .bdi-hl .bdi-hl-zone.bdi-hl-other { opacity: 0.18; }
.bdi-lv { display: flex; flex-wrap: wrap; gap: 8px; font: var(--bdi-font); color: var(--bdi-fg); outline: none; }
.bdi-lv:focus-visible { outline: 2px solid var(--bdi-accent); outline-offset: 2px; }
.bdi-lv-main { display: flex; gap: 6px; flex: 3 1 320px; min-width: 0; }
.bdi-lv-steps { display: flex; flex-direction: column; align-items: stretch; gap: 3px; width: 64px; flex: none; }
.bdi-lv-step { font: inherit; min-height: 36px; background: var(--bdi-chip); color: inherit; border: 1px solid var(--bdi-line); border-radius: 6px; cursor: pointer; }
.bdi-lv-step:disabled { opacity: 0.35; cursor: default; }
.bdi-lv-stack { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; flex: 1 1 auto; overflow-y: auto; }
.bdi-lv-stop { cursor: pointer; border-radius: 4px; text-align: center; font-variant-numeric: tabular-nums; user-select: none; }
.bdi-lv-stop.is-copper { --c: var(--bdi-cu); min-height: 26px; line-height: 26px; background: color-mix(in srgb, var(--c) 30%, var(--bdi-bg)); border-left: 4px solid var(--c); font-weight: 600; }
.bdi-lv-stop.is-dielectric { min-height: 16px; line-height: 16px; font-size: 10px; color: var(--bdi-muted); background: var(--bdi-diel); opacity: 0.8; }
.bdi-lv-stop.is-on { outline: 2px solid var(--bdi-accent); outline-offset: -2px; opacity: 1; }
.bdi-lv-stop.has-net::after { content: ' ●'; color: var(--bdi-hl); text-shadow: 0 0 1px #000; }
.bdi-lv-stage { position: relative; flex: 1 1 auto; min-width: 0; min-height: 280px; border: 1px solid var(--bdi-line); border-radius: 6px; overflow: hidden; background: #10140f; }
.bdi-lv-stage > .bd2-stage { position: absolute; inset: 0; }
.bdi-lv-caption { position: absolute; z-index: 4; left: 6px; top: 6px; padding: 1px 6px; border-radius: 4px; background: rgba(0,0,0,0.55); color: #fff; pointer-events: none; }
.bdi-lv[data-pairing=on] .bdi-lv-stage { outline: 2px dashed var(--bdi-accent); outline-offset: -2px; cursor: crosshair; }
.bdi-lv-side { flex: 1 1 260px; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.bdi-lv-seg { color: var(--bdi-muted); }
.bdi-ctl[hidden], .bdi-lv-route[hidden] { display: none; }
.bdi-ctl { display: flex; flex-direction: column; gap: 6px; padding: 8px; border: 1px solid var(--bdi-line); border-radius: 6px; background: var(--bdi-bg); }
.bdi-ctl-head, .bdi-ctl-row, .bdi-ctl-out { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; }
.bdi-ctl-layer { font: inherit; border: 0; cursor: pointer; } .bdi-ctl-layer.is-on { outline: 1px solid var(--bdi-accent); }
.bdi-ctl-target, .bdi-ctl-tol { flex: 0 0 auto; width: 4.5em; text-align: right; }
.bdi-ctl-z[data-v=ok] { color: var(--bdi-ok); } .bdi-ctl-z[data-v=warn] { color: var(--bdi-warn); } .bdi-ctl-z[data-v=bad] { color: var(--bdi-bad); }
.bdi-ctl-sug { color: var(--bdi-accent); font-variant-numeric: tabular-nums; cursor: help; }
.bdi-ctl-t2 { color: var(--bdi-muted); font-variant-numeric: tabular-nums; min-height: 1.3em; } .bdi-ctl-t2z { color: var(--bdi-fg); font-weight: 600; }
.bdi-ctl-ok { min-height: 32px; font-weight: 600; }
.bdi-lv-route > summary { cursor: pointer; color: var(--bdi-muted); }
@media (max-width: 560px) { .bdi-lv-steps { width: 52px; } .bdi-lv-stage { min-height: 240px; } }
.bdi-tip { position: absolute; z-index: 5; pointer-events: none; background: var(--bdi-bg); color: var(--bdi-fg); font: var(--bdi-font); padding: 2px 6px; border-radius: 4px; box-shadow: 0 1px 4px rgba(0,0,0,0.3); white-space: nowrap; }
`;
