# /// script
# requires-python = ">=3.10"
# dependencies = ["ezdxf>=1.3"]
# ///
"""Turn a finished PEA drawing set (DWG) into a project template, using GstarCAD for Mac.

    uv run solar_gen/make_template.py "example/1 สถานีผลิตน้ำศูนย์ราชการ 280826.dwg"

1. Reads a LibreDWG conversion of the DWG (cached) to find every text that holds project
   data - site name, address, GPS, capacity, counts, engineers, drawing number - by handle.
2. Opens a copy of the real DWG in GstarCAD and replaces those texts with {{PLACEHOLDERS}}.
   Everything else (all sheets, logos, icons, viewports, plot setups) is kept as drawn.
3. Writes templates/<name>.dwg and templates/<name>.json (handle -> placeholder text).

GstarCAD must be running with a drawing open. Handles are stable, so fill_template.py can
later address the same entities directly.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from ezdxf import recover

sys.path.insert(0, str(Path(__file__).parent))
import gstarcad  # noqa: E402
import template  # noqa: E402

HERE = Path(__file__).parent
TEMPLATES = HERE / "templates"

# values that identify the example project, mapped to placeholders (longest first)
EXAMPLE = {
    "site_name": "สถานีผลิตน้ำศูนย์ราชการ การประปาส่วนภูมิภาคสาขากำแพงเพชร",
    "gps": "16.557424, 99.506055",
}
COUNT_WORDS = {"MODULES": "N_MODULES", "SETS": "N_INVERTERS", "STRINGS": "N_STRINGS", "Wp": "MODULE_WP",
               "kW": "INV_KW", "PCS/STRING": "MODULES_PER_STRING"}


def tokenize(text: str, kind: str) -> str:
    t = text.replace(EXAMPLE["site_name"], "{{SITE_NAME}}").replace(EXAMPLE["gps"], "{{GPS}}")
    # numbers often follow an MTEXT code such as \P, so use digit look-arounds instead of \b
    t = re.sub(r"(?<![\d.,])136,800(?![\d.,])", "{{DC_WP}}", t)
    t = re.sub(r"(?<![\d.,])136\.80(?![\d])", "{{DC_KWP2}}", t)
    t = re.sub(r"(?<![\d.,])136\.8(?![\d])", "{{DC_KWP}}", t)
    if kind == "MTEXT" and "kWp" in t:
        # project-description boxes: "240\t\t\tMODULES", "15\t\tSTRINGS", "570\t\tWp" ...
        def count(m):
            word = m.group(3)
            return f"{{{{{COUNT_WORDS[word]}}}}}{m.group(2)}{word}"
        # an MTEXT format group ({\W0.9;\Fcordia.shx|c0;) may sit between the tabs and the word
        t = re.sub(r"(?<![\d/.,}])(\d[\d/.,]*)(\s*\t[\s\t]*(?:\{?(?:\\[A-Za-z][^;]*;)+)?)"
                   r"(PCS/STRING|MODULES|STRINGS|SETS|kW(?!p)|Wp)", count, t)
    t = re.sub(r"Total solar cell \d+ panel", "Total solar cell {{N_MODULES}} panel", t)
    return t


def scan(dxf_path: Path) -> dict:
    doc, _ = recover.readfile(dxf_path)
    entries = []

    def consider(e, where):
        kind = e.dxftype()
        text = e.text if kind == "MTEXT" else e.dxf.text
        new = tokenize(text, kind)
        if new != text:
            entries.append({"handle": e.dxf.handle, "type": kind, "where": where, "original": text, "template": new})

    for e in doc.modelspace():
        if e.dxftype() in ("TEXT", "MTEXT"):
            consider(e, "model")
        elif e.dxftype() == "INSERT":
            for a in e.attribs:
                consider(a, "attrib")

    frame = doc.blocks.get(template.FRAME_BLOCK)
    texts = [e for e in frame if e.dxftype() == "TEXT"]
    for e in frame.query("MTEXT"):   # project name + address cell
        if EXAMPLE["site_name"] in e.text:
            entries.append({"handle": e.dxf.handle, "type": "MTEXT", "where": "title block", "original": e.text,
                            "template": "{{SITE_NAME}}\\P{{SITE_ADDRESS}}"})
    # engineers: name / licence texts on the HEAD layer, matched to the nearest role row
    rows = {r: next(e for e in texts if e.dxf.text.strip() == f"{r.upper()} ENGINEER") for r in ("structural", "electrical")}
    for e in texts:
        if e.dxf.layer != "HEAD":
            continue
        role = min(rows, key=lambda r: abs(rows[r].dxf.insert.y - e.dxf.insert.y))
        field = "LIC" if any(c.isdigit() for c in e.dxf.text) else "NAME"
        entries.append({"handle": e.dxf.handle, "type": "TEXT", "where": "title block", "original": e.dxf.text,
                        "template": f"{{{{ENG_{role[:3].upper()}_{field}}}}}"})
    # drawing number: one single-character text per box, left to right
    boxes = sorted((e for e in texts if len(e.dxf.text.strip()) == 1 and e.dxf.layer == template.FRAME_BLOCK_LAYER
                    and abs(e.dxf.height - 1.55 / 7.8469) < 0.004), key=lambda e: e.dxf.insert.x)
    number = "".join(e.dxf.text for e in boxes)
    return {"entries": entries, "drawing_no": {"handles": [e.dxf.handle for e in boxes], "original": number,
                                               "template": re.sub(r"\d", "#", number)}}


KEY_HELP = {
    "SITE_NAME": "ชื่อสถานที่/โครงการ (site name) - appears in sheet titles, title block, cover",
    "SITE_ADDRESS": "ที่อยู่ (address line under the project name in the title block)",
    "GPS": "LAT, LONG",
    "DC_KWP": "DC capacity, 1 decimal (kWp)",
    "DC_KWP2": "DC capacity, 2 decimals (kWp)",
    "DC_WP": "DC capacity in Wp with thousands separator",
    "N_MODULES": "number of PV modules",
    "N_INVERTERS": "number of inverters",
    "N_STRINGS": "number of strings",
    "MODULE_WP": "module power (Wp)",
    "INV_KW": "inverter AC power (kW)",
    "MODULES_PER_STRING": "modules per string, e.g. 16 or 16/15",
    "ENG_STR_NAME": "structural engineer name",
    "ENG_STR_LIC": "structural engineer licence no.",
    "ENG_ELE_NAME": "electrical engineer name",
    "ENG_ELE_LIC": "electrical engineer licence no.",
    "DRAWING_NO": "drawing number, same pattern as the template",
}


def _example_value(manifest: dict, key: str) -> str:
    """The example project's value for a placeholder (read back from the original text)."""
    if key == "DRAWING_NO":
        return manifest["drawing_no"]["original"]
    for e in manifest["entries"]:
        tpl, orig = e["template"], e["original"]
        if "{{" + key + "}}" not in tpl:
            continue
        parts = re.split(r"(\{\{\w+\}\})", tpl)
        pattern = "".join("(?P<%s>.*?)" % p[2:-2] if p.startswith("{{") else re.escape(p) for p in parts)
        try:
            m = re.fullmatch(pattern, orig, re.S)
        except re.error:   # same placeholder twice in one text
            continue
        if m:
            return m.group(key).strip()
    return ""


def write_config_text(path: Path, manifest: dict, tokens: list[str]) -> None:
    keys = [k for k in KEY_HELP if k in tokens or k == "DRAWING_NO"] + [k for k in tokens if k not in KEY_HELP]
    lines = ["# PEA solar-rooftop template - project values",
             f"# Template: {path.with_suffix('.dwg').name}  (made from: {manifest.get('source', '?')})",
             "# Copy this file for a project, edit the values, then run:",
             "#   uv run solar_gen/fill_template.py <this-file>",
             "# Format: KEY = value   (one per line, UTF-8, Thai OK, # starts a comment)",
             "# Values below are the example project's, as found in the template source.", "",
             "TEMPLATE = " + path.with_suffix(".dwg").name,
             "OUTPUT_NAME = NEW-PROJECT", ""]
    for k in keys:
        lines.append(f"# {KEY_HELP.get(k, k)}")
        lines.append(f"{k} = {_example_value(manifest, k)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


lisp_set_text = gstarcad.lisp_set_text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dwg", type=Path)
    ap.add_argument("--name", default="PEA-solar-template")
    ap.add_argument("--dry-run", action="store_true", help="only write the manifest")
    args = ap.parse_args()

    dwg = args.dwg.resolve()
    manifest = scan(template.convert_dwg(dwg))
    manifest["source"] = dwg.name
    TEMPLATES.mkdir(exist_ok=True)
    (TEMPLATES / f"{args.name}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    tokens = sorted({m for e in manifest["entries"] for m in re.findall(r"\{\{(\w+)\}\}", e["template"])})
    write_config_text(TEMPLATES / f"{args.name}.txt", manifest, tokens)
    print(f"{len(manifest['entries'])} texts -> placeholders: {', '.join(tokens)}")
    print(f"drawing number {manifest['drawing_no']['original']} -> {manifest['drawing_no']['template']}")
    if args.dry_run:
        return 0

    # GstarCAD works on an ASCII-named copy (script prompts break on spaces / non-ASCII)
    gstarcad.WORK.mkdir(parents=True, exist_ok=True)
    src = gstarcad.WORK / "template_source.dwg"
    shutil.copyfile(dwg, src)
    out = (TEMPLATES / f"{args.name}.dwg").resolve()
    staged = gstarcad.WORK / "_template_build.dwg"   # unique name: the old template may be open in CAD
    lines = gstarcad.open_drawing(src)
    lines += [lisp_set_text(e["handle"], e["template"]) for e in manifest["entries"]]
    lines += [lisp_set_text(h, ch) for h, ch in zip(manifest["drawing_no"]["handles"], manifest["drawing_no"]["template"])]
    staged.unlink(missing_ok=True)
    lines += ['(command "_.REGEN")'] + gstarcad.save_as(staged)
    lines += ['(setq __result (getvar "DWGNAME"))']
    print("GstarCAD:", gstarcad.run(lines, timeout=300, label="make-template"))
    shutil.copyfile(staged, out)
    print(f"Template  {out}")
    print(f"Manifest  {TEMPLATES / (args.name + '.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
