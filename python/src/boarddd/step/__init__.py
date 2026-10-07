"""STEP: the assembly tree, placements and refdes as text (`text`), slimming (`slim`), model-file sniffing
(`modelfile`) and STEP-to-placement registration (`registration`), all stdlib.

The ``[step]`` extra (``pip install "boarddd[step]"``: cadquery-ocp, numpy, shapely) adds the OpenCascade
engine: `occ` (reading, libGL workaround), `index` (a board's text index and per-product cut-outs), `split`
(a board STEP into measured, fingerprinted components with STEP/GLB exports), `measure`, `fingerprint`,
`work` (process pool, hooks), `cache` (the model cache), `hlr` (SVG line drawings) and `cli`. Importing one of
those without the extra raises an ImportError naming it. See docs/step.md.
"""
