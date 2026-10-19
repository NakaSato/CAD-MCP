# /// script
# requires-python = ">=3.10"
# dependencies = ["pypdf>=4"]
# ///
"""Fill a PEA solar template DWG with one project's values, using GstarCAD for Mac.

    uv run solar_gen/fill_template.py solar_gen/projects/sample.template.txt [--no-plot]

The config is a plain text file of KEY = value lines (see templates/<template>.txt for every key).
Output: solar_gen/out/<OUTPUT_NAME>/<OUTPUT_NAME>.dwg and, unless --no-plot, one PDF of all
layouts plotted by GstarCAD (DWG To PDF, ISO full bleed A3).
GstarCAD must be running with a drawing open.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gstarcad  # noqa: E402
from gstarcad import lisp_set_text  # noqa: E402

HERE = Path(__file__).parent


def read_config(path: Path) -> dict[str, str]:
    values = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"{path.name}:{n}: expected KEY = value, got {raw!r}")
        key, value = line.split("=", 1)
        values[key.strip().upper()] = value.strip()
    return values


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("config", type=Path)
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()

    cfg = read_config(args.config)
    tpl = Path(cfg.get("TEMPLATE", "PEA-solar-template.dwg"))
    if not tpl.is_absolute():
        tpl = next((p for p in (args.config.parent / tpl, HERE / "templates" / tpl) if p.exists()), tpl)
    manifest = json.loads(tpl.with_suffix(".json").read_text(encoding="utf-8"))
    name = cfg.get("OUTPUT_NAME", "NEW-PROJECT")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise SystemExit("OUTPUT_NAME must be ASCII letters, digits, - _ . (it becomes the file name)")

    needed = sorted({k for e in manifest["entries"] for k in re.findall(r"\{\{(\w+)\}\}", e["template"])})
    missing = [k for k in needed if not cfg.get(k)]
    if missing:
        raise SystemExit(f"missing values in {args.config.name}: {', '.join(missing)}")
    dn, boxes = cfg.get("DRAWING_NO", ""), manifest["drawing_no"]["handles"]
    if len(dn) != len(boxes):
        raise SystemExit(f"DRAWING_NO must have {len(boxes)} characters like {manifest['drawing_no']['template']}")

    fill = lambda t: re.sub(r"\{\{(\w+)\}\}", lambda m: cfg[m.group(1)], t)
    gstarcad.WORK.mkdir(parents=True, exist_ok=True)
    src = gstarcad.WORK / "fill_source.dwg"
    shutil.copyfile(tpl, src)
    staged = gstarcad.WORK / f"{name}.dwg"
    staged.unlink(missing_ok=True)
    for old in gstarcad.WORK.glob(f"{name}-*.pdf"):
        old.unlink()

    lines = gstarcad.open_drawing(src)
    lines += [lisp_set_text(e["handle"], fill(e["template"])) for e in manifest["entries"]]
    lines += [lisp_set_text(h, ch) for h, ch in zip(boxes, dn)]
    lines += ['(command "_.REGEN")'] + gstarcad.save_as(staged)
    if not args.no_plot:
        lines += gstarcad.plot_all_layouts()
    lines += ['(setq __result (vl-prin1-to-string (layoutlist)))']
    print(f"GstarCAD: filling {len(manifest['entries'])} texts in {tpl.name}" + ("" if args.no_plot else " and plotting"))
    result = gstarcad.run(lines, timeout=1800, label="fill")

    out_dir = HERE / "out" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    dwg = out_dir / f"{name}.dwg"
    shutil.copyfile(staged, dwg)
    print(f"DWG     {dwg}")
    if not args.no_plot:
        from pypdf import PdfWriter
        layouts = re.findall(r'"((?:[^"\\]|\\.)*)"', result)
        writer, used = PdfWriter(), 0
        for lay in layouts:
            pdf = gstarcad.WORK / f"{name}-{lay}.pdf"
            if pdf.exists():
                writer.append(str(pdf))
                used += 1
        pdf_out = out_dir / f"{name}.pdf"
        with open(pdf_out, "wb") as fh:
            writer.write(fh)
        print(f"PDF     {pdf_out}  ({used}/{len(layouts)} layouts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
