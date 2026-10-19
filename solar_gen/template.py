"""Template handling: turn the example DWG into a small cached DXF holding the PEA frame block.

The full example converts to ~86 MB of DXF, so we do it once and keep only what we reuse.
Needs LibreDWG's `dwg2dxf` on PATH (macOS: `brew install libredwg`).
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from pathlib import Path

import ezdxf
from ezdxf import bbox, recover
from ezdxf.addons import Importer

log = logging.getLogger("solar_gen.template")

FRAME_BLOCK = "Tiltle block WHA"   # spelling as in the PEA template
FRAME_BLOCK_LAYER = "กรอบPEA"
SYMBOL_BLOCKS = ["PV PANEL", "DC FUSE", "DC SPD"]   # single-line diagram symbols from the example
CACHE = Path(__file__).parent / ".cache"


def _key(dwg: Path) -> str:
    st = dwg.stat()
    return hashlib.sha1(f"{dwg.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:12]


def convert_dwg(dwg: Path) -> Path:
    """DWG -> DXF with LibreDWG, cached by file identity."""
    CACHE.mkdir(exist_ok=True)
    out = CACHE / f"template-{_key(dwg)}.dxf"
    if out.exists():
        return out
    if not shutil.which("dwg2dxf"):
        raise RuntimeError("dwg2dxf not found - install LibreDWG (macOS: brew install libredwg)")
    log.info("Converting %s (one-off, may take a minute)", dwg.name)
    tmp_dwg = CACHE / "source.dwg"           # dwg2dxf struggles with some unicode paths
    shutil.copyfile(dwg, tmp_dwg)
    subprocess.run(["dwg2dxf", "-y", "-o", str(out), str(tmp_dwg)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    tmp_dwg.unlink(missing_ok=True)
    if not out.exists():
        raise RuntimeError(f"dwg2dxf failed to convert {dwg}")
    return out


def frame_template(dwg: Path) -> tuple[Path, dict]:
    """Return a slim DXF holding only the frame block, plus how to place it on a sheet.

    The placement dict gives the block insert offset and scale relative to the frame's
    lower-left corner, measured from an existing sheet in the template.
    """
    slim = CACHE / f"frame-{_key(dwg)}.dxf"
    meta = slim.with_suffix(".json")
    if slim.exists() and meta.exists():
        return slim, json.loads(meta.read_text())

    full, _ = recover.readfile(convert_dwg(dwg))
    ref = next(e for e in full.modelspace().query("INSERT") if e.dxf.name == FRAME_BLOCK)
    box = bbox.extents([ref], fast=True)
    placement = {
        "dx": ref.dxf.insert.x - box.extmin.x,
        "dy": ref.dxf.insert.y - box.extmin.y,
        "scale": ref.dxf.xscale,
        "size": [box.size.x, box.size.y],
    }
    doc = ezdxf.new("R2018", setup=True)
    imp = Importer(full, doc)
    imp.import_block(FRAME_BLOCK)
    imp.import_blocks([b for b in SYMBOL_BLOCKS if b in full.blocks])
    imp.finalize()
    doc.saveas(slim)
    logo = extract_logo(full, CACHE / f"logo-{_key(dwg)}.png")
    placement["logo"] = logo.name if logo else None
    meta.write_text(json.dumps(placement))
    return slim, placement


def extract_logo(full, out: Path) -> Path | None:
    """Save the owner logo embedded (as an OLE picture) in the frame block.

    LibreDWG keeps the OLE data but not its position, so the logo is re-placed as an IMAGE.
    The OLE blob is a 128-byte header followed by a compound file whose CONTENTS stream is a BMP.
    """
    try:
        import io

        import olefile
        from PIL import Image
        for e in full.blocks.get(FRAME_BLOCK).query("OLE2FRAME"):
            ole = olefile.OleFileIO(io.BytesIO(e.binary_data()[128:]))
            if ole.exists("CONTENTS"):
                data = ole.openstream("CONTENTS").read()
                if data[:2] == b"BM":
                    Image.open(io.BytesIO(data)).convert("RGB").save(out)
                    return out
    except Exception as exc:   # logo is optional
        log.warning("Could not extract the logo from the template: %s", exc)
    return None


def fill_title_block(doc, cfg: dict) -> None:
    """Write the project identity into the (shared) frame block definition."""
    blk = doc.blocks.get(FRAME_BLOCK)
    p, eng = cfg["project"], cfg["engineers"]
    texts = [e for e in blk if e.dxftype() == "TEXT"]

    def by_text(s):
        return next((e for e in texts if e.dxf.text.strip() == s), None)

    # owner name and address (the two tall texts at the top of the owner cell)
    owner = by_text("การประปาส่วนภูมิภาค")
    if owner is not None:
        owner.dxf.text = p["owner"]
    addr = next((e for e in texts if e.dxf.text.startswith("เลขที่")), None)
    if addr is not None:
        addr.dxf.text = p["owner_address"]

    # project name + address (one MTEXT)
    for e in blk.query("MTEXT"):
        e.text = f"{p['name']}\\P{p['address']}"

    # drawing number: one TEXT per character box, left to right
    label = by_text("REV.")
    boxes = sorted((e for e in texts if len(e.dxf.text.strip()) == 1 and abs(e.dxf.height - 1.55 / 7.8469) < 0.004
                    and e.dxf.layer == "กรอบPEA"), key=lambda e: e.dxf.insert.x)
    number = p["drawing_no"]
    if len(boxes) == len(number):
        for e, ch in zip(boxes, number):
            e.dxf.text = ch
    else:
        log.warning("drawing_no %r has %d characters, title block has %d boxes - left unchanged",
                    number, len(number), len(boxes))
    if label is not None:   # the revision value sits just below the REV. label
        rev = min((e for e in texts if e.dxf.text.strip().isdigit() and e is not label
                   and abs(e.dxf.insert.x - label.dxf.insert.x) < 1.0),
                  key=lambda e: abs(e.dxf.insert.y - label.dxf.insert.y), default=None)
        if rev is not None:
            rev.dxf.text = str(p.get("revision", 0))

    # engineers: match each name/licence text to the nearest role row
    rows = {role: by_text(f"{role.upper()} ENGINEER") for role in ("structural", "electrical")}
    for e in texts:
        if e.dxf.layer != "HEAD":
            continue
        role = min(rows, key=lambda r: abs(rows[r].dxf.insert.y - e.dxf.insert.y) if rows[r] else 1e9)
        is_licence = any(ch.isdigit() for ch in e.dxf.text) or e.dxf.text.startswith(("สย", "สฟ", "ภย", "ภฟ"))
        e.dxf.text = eng[role]["licence"] if is_licence else eng[role]["name"]
