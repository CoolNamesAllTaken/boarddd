// boarddd/impedance/ui: click-a-trace impedance components for a view2d stage (docs/impedance.md "UI").
//   impedancePanel(el, { board, copper, stage })   the panel: pick, analyse in a Worker, Z, profile, cross-section
//   crossSection(el, section, { board })           an SVG cross-section of an impedance@1 section
//   copperHitIndex / attachPicker / createHighlight / copperContent   the stage pieces on their own
//   createAnalyzer                                 analyzeNet in a Worker (main thread on file://)
//   boardPointFromPick                             a boarddd/scene pick on the board solid → board mm
export { impedancePanel, verdict, fmtHz, STRUCTURES, DISCONTINUITIES } from './panel.js';
export { crossSection, sectionLayout, sectionGeometry } from './crosssection.js';
export { copperHitIndex, pairOf, pairOrder, trackPath, arcPoints } from './pick.js';
export { attachPicker, createHighlight, copperContent, routeSlice, routePoint, boardPointFromPick, LAYER_COLORS } from './stage.js';
export { createAnalyzer } from './analyzer.js';
export { IMPEDANCE_UI_CSS } from './style.js';
