"""Generate the net-class / impedance fixtures of the CAD readers (docs/nets.md "From the CAD files"):

    python fixtures/cad/make_cad.py          # rewrites the files below
    python fixtures/cad/make_cad.py --check  # fails if they differ

* pic_programmer-impedance-ipc2581.xml.gz: KiCad's IPC-2581 export of the pic_programmer demo
  (fixtures/pic_programmer/exchange) with impedance Specs added as IPC-2581C writes them: SE50 (SingleEnded
  microstrip, ±10 %) referenced from /VPP_ON's Sets on top_layer; DP90 (EdgeCoupled, Pitch, ±9 ohm) from the
  LogicalNets of /PC-CLOCK-OUT and /PC-DATA-OUT, a netPair; CPW55 (CoplanarWaveguide, ground spacing, two
  reference planes) from /DATA-RB7 on bottom_layer; and Z75 in the rev B form (one Impedance per type, with
  Property) from /CLOCK-RB6's LogicalNet.
* pic_programmer-impedance-odb.zip: KiCad's ODB++ export of the same board with net attributes added to
  eda/data (.diff_pair, .electrical_class DP_90 / SE_50, .dpair_gap) and steps/pcb/impedance.xml with two
  Descriptors (50 ohm, 90 ohm with a spacing) whose ids the bottom_layer tracks of /VPP_ON and of /PC-CLOCK-OUT +
  /PC-DATA-OUT carry as .imp_constraint_id.
* usb.brd: a synthetic Eagle 9 board: classes default / DP_90 / 50ohm, a USB pair on Top with an arc, a 50 ohm
  RF line, a via to Bottom.
* impedance.csv, impedance.json, impedance_control.xlsx: impedance files (boarddd.io.impedance_file); the xlsx as a
  fab's sheet would be, a title row above the header and no nets.
"""

from __future__ import annotations

import gzip
import io
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIC = HERE.parent / "pic_programmer" / "exchange"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)

SPECS = """      <Spec name="SE50">
        <Impedance value="50" tolPlus="10" tolMinus="10" tolPercent="true">
          <SingleEnded structure="MICROSTRIP_MASK_COVERED">
            <LineWidth value="0.5" unit="MM"/>
            <RefPlane layerOrGroupRef="bottom_layer"/>
          </SingleEnded>
        </Impedance>
      </Spec>
      <Spec name="DP90">
        <Impedance value="90" tolPlus="9" tolMinus="9">
          <EdgeCoupled structure="MICROSTRIP_NO_MASK">
            <LineWidth value="8" unit="MILS"/>
            <Pitch value="0.4" unit="MM"/>
          </EdgeCoupled>
        </Impedance>
      </Spec>
      <Spec name="CPW55">
        <Impedance value="55" tolPlus="5" tolMinus="5" tolPercent="true">
          <CoplanarWaveguide structure="COPLANAR_WAVEGUIDE_MASK_COVERED">
            <LineWidth value="0.3"/>
            <Spacing value="0.2"/>
            <CoplanarGroundSpacing value="0.15"/>
            <RefPlane layerOrGroupRef="top_layer"/>
          </CoplanarWaveguide>
        </Impedance>
      </Spec>
      <Spec name="Z75">
        <Impedance type="IMPEDANCE"><Property value="75" unit="OHMS" tolPlus="7.5" tolMinus="7.5"/></Impedance>
        <Impedance type="LINEWIDTH"><Property value="0.25" unit="MM"/></Impedance>
      </Spec>
"""
LOGICAL = """        <LogicalNet name="/PC-CLOCK-OUT" netPair="/PC-DATA-OUT">
          <PinRef componentRef="J1" pin="1"/>
          <SpecRef id="DP90"/>
        </LogicalNet>
        <LogicalNet name="/PC-DATA-OUT" netPair="/PC-CLOCK-OUT">
          <PinRef componentRef="J1" pin="2"/>
          <SpecRef id="DP90"/>
        </LogicalNet>
        <LogicalNet name="/CLOCK-RB6">
          <PinRef componentRef="J1" pin="4"/>
          <SpecRef id="Z75"/>
        </LogicalNet>
"""


def _spec_refs(xml: str, layer: str, net: str, spec: str) -> str:
    """Add <SpecRef id=spec/> to every Set of `net` in the LayerFeature of `layer`."""
    start = xml.index(f'<LayerFeature layerRef="{layer}">')
    end = xml.index("</LayerFeature>", start)
    body = xml[start:end].replace(f'<Set net="{net}">', f'<Set net="{net}">\n            <SpecRef id="{spec}"/>')
    return xml[:start] + body + xml[end:]


def ipc2581() -> bytes:
    xml = gzip.decompress((PIC / "pic_programmer-ipc2581.xml.gz").read_bytes()).decode("utf-8")
    xml = xml.replace("    </CadHeader>", SPECS + "    </CadHeader>", 1)
    first = xml.index("        <LayerFeature ")
    xml = xml[:first] + LOGICAL + xml[first:]
    xml = _spec_refs(xml, "top_layer", "/VPP_ON", "SE50")
    xml = _spec_refs(xml, "bottom_layer", "/DATA-RB7", "CPW55")
    return gzip.compress(xml.encode("utf-8"), compresslevel=9, mtime=0)


IMPEDANCE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Impedances Version="8.1" MaxImpIdValUsed="2">
 <Descriptor id="1" TraceLayerName="bottom_layer">
  <RequiredImpedance ValOhms="50" PlusVal="5" MinusVal="5" ValPercent="true"/>
  <OriginalTraceWidth Val="0.5" Units="MM"/>
  <RefPlane Val="3"/>
 </Descriptor>
 <Descriptor id="2" TraceLayerName="bottom_layer">
  <RequiredImpedance ValOhms="90" PlusVal="9" MinusVal="9" ValPercent="false"/>
  <OriginalTraceWidth Val="8" Units="MIL"/>
  <OriginalSpacing Val="6"/>
 </Descriptor>
</Impedances>
"""


def _eda(text: str) -> str:
    table = "@0 .diff_pair\n@1 .electrical_class\n@2 .dpair_gap\n&0 PC_CLK\n&1 DP_90\n&2 SE_50\n"
    lines = text.splitlines()
    out = []
    done = False
    for line in lines:
        if line.startswith("NET ") and not done:
            out.append(table.rstrip("\n"))
            done = True
        if line.startswith("NET "):
            name = line[4:].split(";")[0].strip()
            if name in ("/PC-CLOCK-OUT", "/PC-DATA-OUT"):
                line = f"NET {name};0=0,1=1,2=0.15"
            elif name == "/VPP_ON":
                line = f"NET {name};1=2"
        out.append(line)
    return "\n".join(out) + "\n"


NETS = {"/VPP_ON": 1, "/PC-CLOCK-OUT": 2, "/PC-DATA-OUT": 2}


def _track_features(eda: str, layer: str) -> dict[str, list[int]]:
    """The line features (SNT TRC FIDs) of each net in NETS on `layer`."""
    layers, net, snt, out = [], None, None, {}
    for line in eda.splitlines():
        if line.startswith("LYR "):
            layers = line.split()[1:]
        elif line.startswith("NET "):
            net = line[4:].split(";")[0].strip()
        elif line.startswith("SNT "):
            snt = line.split()[1]
        elif line.startswith("FID ") and net in NETS and snt == "TRC":
            t = line.split()
            if layers[int(t[2])] == layer:
                out.setdefault(net, []).append(int(t[3]))
    return out


def _features_with_constraint(features: str, tracks: dict[str, list[int]]) -> str:
    """Tag the nets' line features with .imp_constraint_id (NETS) and declare the attribute."""
    want = {i: NETS[net] for net, idx in tracks.items() for i in idx}
    out, index, in_surface, declared = [], -1, False, False
    for line in features.splitlines():
        t = line.split()
        head = t[0] if t else ""
        if not declared and head in ("P", "L", "A", "T", "B", "S"):
            out.append("@9 .imp_constraint_id")
            declared = True
        if in_surface:
            in_surface = head != "SE"
            out.append(line)
            continue
        if head in ("P", "L", "A", "T", "B", "S"):
            index += 1
            in_surface = head == "S"
            if index in want:
                body, _, rest = line.partition(";")
                attrs, _, tail = rest.partition(";")
                if attrs.startswith("ID="):
                    attrs, tail = "", attrs
                attrs = ",".join(x for x in (attrs.strip(), f"9={want[index]}") if x)
                line = f"{body.rstrip()} ;{attrs}" + (f";{tail}" if tail else "")
        out.append(line)
    return "\n".join(out) + "\n"


def odb() -> bytes:
    src = zipfile.ZipFile(PIC / "pic_programmer-odb.zip")
    names = src.namelist()
    eda_name = next(n for n in names if n.endswith("steps/pcb/eda/data"))
    root = eda_name[: -len("steps/pcb/eda/data")]
    features_name = f"{root}steps/pcb/layers/bottom_layer/features"
    features = src.read(features_name).decode("utf-8")
    eda = _eda(src.read(eda_name).decode("utf-8"))
    tracks = _track_features(eda, "bottom_layer")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in sorted(names):
            data = src.read(n)
            if n == eda_name:
                data = eda.encode("utf-8")
            elif n == features_name:
                data = _features_with_constraint(features, tracks).encode("utf-8")
            info = zipfile.ZipInfo(n, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
        info = zipfile.ZipInfo(f"{root}steps/pcb/impedance.xml", FIXED_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, IMPEDANCE_XML)
    return out.getvalue()


EAGLE = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE eagle SYSTEM "eagle.dtd">
<eagle version="9.6.2">
<drawing>
<settings><setting alwaysvectorfont="no"/></settings>
<grid distance="0.1" unitdist="mm" unit="mm"/>
<layers>
<layer number="1" name="Top" color="4" fill="1" visible="yes" active="yes"/>
<layer number="2" name="Route2" color="16" fill="1" visible="yes" active="yes"/>
<layer number="16" name="Bottom" color="1" fill="1" visible="yes" active="yes"/>
</layers>
<board>
<plain>
<wire x1="0" y1="0" x2="30" y2="0" width="0" layer="20"/>
</plain>
<classes>
<class number="0" name="default" width="0" drill="0">
</class>
<class number="1" name="DP_90" width="0.2" drill="0.3">
<clearance class="1" value="0.15"/>
</class>
<class number="2" name="50ohm" width="0.35" drill="0">
<clearance class="0" value="0.2"/>
</class>
</classes>
<signals>
<signal name="USB_D+" class="1">
<wire x1="2" y1="5.175" x2="12" y2="5.175" width="0.2" layer="1"/>
<wire x1="12" y1="5.175" x2="14" y2="7.175" width="0.2" layer="1" curve="90"/>
<wire x1="14" y1="7.175" x2="14" y2="12" width="0.2" layer="1"/>
</signal>
<signal name="USB_D-" class="1">
<wire x1="2" y1="4.825" x2="12" y2="4.825" width="0.2" layer="1"/>
<wire x1="12" y1="4.825" x2="14.35" y2="7.175" width="0.2" layer="1" curve="90"/>
<wire x1="14.35" y1="7.175" x2="14.35" y2="12" width="0.2" layer="1"/>
</signal>
<signal name="RF_OUT" class="2">
<wire x1="2" y1="15" x2="20" y2="15" width="0.35" layer="1"/>
<via x="20" y="15" extent="1-16" drill="0.3" diameter="0.6"/>
<wire x1="20" y1="15" x2="28" y2="15" width="0.35" layer="16"/>
</signal>
<signal name="GND">
<wire x1="2" y1="20" x2="28" y2="20" width="0.5" layer="16"/>
</signal>
</signals>
</board>
</drawing>
</eagle>
"""

CSV = """class,target,tolerance,kind,nets,layer,width,gap
USB,90 ohm,±10 %,diff,"USB_D{P,N}",F.Cu,0.2,0.15
USB,90,10,differential,"USB_D{P,N}",B.Cu,0.18,0.17
RF,50R,5 ohm,single,RF_* ANT_*,,,
CLK,,10,,CLK*,,,
DDR,40,,,"DDR_DQ[0-7] DDR_DQS*",L3,4 mil,
"""

JSON_DOC = """{
  "classes": [
    {"class": "PCIE", "target": 85, "tolerance": 10, "kind": "differential", "nets": ["*PCIE*_{P,N}"],
     "layer": "In2.Cu", "width": 0.11, "gap": 0.13, "reference": "In1.Cu In3.Cu"},
    {"name": "RF", "z0": "50 Ω", "nets": "RF_*", "structure": "CPWG", "coplanar gap": 0.15},
    {"name": "LVDS", "Zdiff": 100, "nets": "LVDS*"}
  ]
}
"""


def xlsx() -> bytes:
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Impedance"
    sheet.append(["Impedance control requirements"])
    sheet.append([])
    sheet.append(
        [
            "Layer",
            "Type",
            "Impedance (Ω)",
            "Tolerance (%)",
            "Line width (mil)",
            "Spacing (mil)",
            "Reference",
        ]
    )
    sheet.append(["L1", "Single-ended", 50, 10, "12 mil", None, "L2"])
    sheet.append(["L1", "Differential", 90, 10, "6 mil", "6 mil", "L2"])
    sheet.append(["L4", "Differential", 100, 10, "5 mil", "7 mil", "L3"])
    out = io.BytesIO()
    book.properties.created = book.properties.modified = __import__("datetime").datetime(2026, 1, 1)
    book.save(out)
    # openpyxl writes timestamps into docProps/core.xml and zip entries: rewrite with fixed times
    src = zipfile.ZipFile(io.BytesIO(out.getvalue()))
    fixed = io.BytesIO()
    with zipfile.ZipFile(fixed, "w", zipfile.ZIP_DEFLATED) as z:
        for n in src.namelist():
            data = src.read(n)
            if n == "docProps/core.xml":
                data = re.sub(
                    rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*",
                    rb"\g<1>2026-01-01T00:00:00Z",
                    data,
                )
            info = zipfile.ZipInfo(n, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return fixed.getvalue()


def _same(name: str, old: bytes, new: bytes) -> bool:
    """Byte for byte, but a spreadsheet by its cells (openpyxl versions write different bytes)."""
    if not name.endswith(".xlsx"):
        return old == new
    import openpyxl

    def cells(data: bytes):
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True)
        return [[list(r) for r in sheet.iter_rows(values_only=True)] for sheet in book.worksheets]

    return cells(old) == cells(new)


def main() -> int:
    files = {
        "pic_programmer-impedance-ipc2581.xml.gz": ipc2581(),
        "pic_programmer-impedance-odb.zip": odb(),
        "usb.brd": EAGLE.encode("utf-8"),
        "impedance.csv": CSV.encode("utf-8"),
        "impedance.json": JSON_DOC.encode("utf-8"),
        "impedance_control.xlsx": xlsx(),
    }
    stale = [n for n, data in files.items() if not (HERE / n).is_file() or not _same(n, (HERE / n).read_bytes(), data)]
    if "--check" in sys.argv:
        if stale:
            print(
                f"stale: {', '.join(stale)}: run python fixtures/cad/make_cad.py",
                file=sys.stderr,
            )
            return 1
        return 0
    for n, data in files.items():
        (HERE / n).write_bytes(data)
    print(f"wrote {len(files)} files ({len(stale)} changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
