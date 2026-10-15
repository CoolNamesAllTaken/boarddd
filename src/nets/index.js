// boarddd/nets: choosing nets by group (docs/nets.md). Net-name patterns (wildcards, alternatives, pairs, regex)
// and tracks like a picked one (same width on the same layer; for a pair, the same gap). Python: boarddd.nets.
export { parsePattern, matchesPattern, matchNets, pairsAmong } from './patterns.js';
export { similarTracks, pairGap, widthTolerance, gapTolerance } from './similar.js';
