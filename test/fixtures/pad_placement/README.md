# pad_placement fixtures

Copied from [kipr](https://github.com/CoolNamesAllTaken/kipr) `tests/library/fixtures/pad_placement/`
(commit edc53a3, kipr PR #13 "draw pad copper at KiCad's shape offset, holes at the pad position"), MIT.
`golden.json` holds KiCad's own numbers (pcbnew: pad position, shape position = copper centre,
effective-shape bbox, hole position, 3D model offset/rotate/scale), made by `make_golden.py` with
KiCad 10's `pcbnew` module.

The footprints: `R_0603_1608Metric` and `USB_C_Receptacle_CNCTech_C-ARA1-AK51X`,
`SMA_Amphenol_132289_EdgeMount` are from KiCad's stock footprint library (CC-BY-SA 4.0 with the KiCad
libraries exception); `RP2040-Zero_Castellated` is from the kicad-libs repository the kipr PR was about.
