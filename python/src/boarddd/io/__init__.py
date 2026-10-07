"""Format readers: fab packages (Gerber, Excellon, gbrjob, pick-and-place, BOM) into the board model.

Stdlib only. `boarddd.io.package.read_package` is the one entry point that assembles a
:class:`boarddd.model.Board` from a folder or zip; the modules below are the readers it uses.
"""
