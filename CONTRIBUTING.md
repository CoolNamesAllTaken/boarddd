# Contributing to boarddd

boarddd is the one library for PCB rendering and board/part ingestion across our projects (kipr
and internal tools). One repository, two packages, one version tag (`vX.Y.Z` covers both):

- the npm package `boarddd` (`src/`): framework-free ES modules for the browser, `.d.ts` typings;
- the Python package `boarddd` (`python/`, `pip install "boarddd @ git+…@vX.Y.Z#subdirectory=python"`).

## The rule

**Readers are server-side (Python), renderers are browser-side (JS); one board model in between.**

- File formats (Gerber/X2 attributes, Excellon, gbrjob, pos, BOM, KiCad files, ODB++, IPC-2581, STEP)
  are parsed in `python/src/boarddd/` into the normalised board model, `boarddd/board@1`
  ([docs/model.md](docs/model.md)), written as `board.json`.
- The browser draws from `board.json` plus the raw Gerber/drill text for the wasm renderer. The only
  JS parsers are the ones that must run without a server: Gerber/Excellon/ODB++ layers (wasm),
  `.kicad_mod` (library viewer), and Gerber-only uploads.
- The model's dataclasses (`python/src/boarddd/model.py`) own the format. `schema/board.schema.json`
  and `src/model/{schema.js,board.d.ts}` are generated from them: run `python -m boarddd.model --write`
  after changing the model; CI fails when they're stale.
- boarddd draws boards and exposes events; app chrome (toolbars, tables, routes, i18n) stays in the apps.

## Fixtures

Shared golden data lives in `fixtures/` (used by node, Playwright and pytest); `test/fixtures/` holds
JS-only test inputs. Only KiCad demo boards, magpie's synthetic boards and generated data go in: no
company or customer designs. Every fixture is listed with its licence in
[fixtures/LICENSES.md](fixtures/LICENSES.md).

## Checks

```bash
npm ci && npm test && npm run typecheck      # JS unit tests + typings
npm run test:browser                          # Playwright (npx playwright install chromium first)
cd python && pip install -e ".[dev]" && pytest && ruff check . ../fixtures && ruff format --check . ../fixtures
python -m boarddd.model --check               # generated schema/typings are up to date
```

The JS package version (`package.json`) and the Python one (`python/pyproject.toml`,
`boarddd.__version__`) are bumped together at each release; a test checks they match.
