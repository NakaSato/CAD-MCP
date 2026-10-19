# /// script
# requires-python = ">=3.10"
# dependencies = ["ezdxf>=1.3", "matplotlib>=3.8", "pyyaml>=6", "pillow>=10", "olefile>=0.47"]
# ///
"""Generate a PEA solar-rooftop drawing set from a project file.

    uv run solar_gen/generate.py solar_gen/projects/sample.yaml [--no-plot]

Writes to solar_gen/out/<output_name>/: <name>.dxf, <name>.pdf, sheet PNGs, check_report.md.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

import ezdxf
import yaml
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import design  # noqa: E402
import drawing  # noqa: E402
import template  # noqa: E402


def report(cfg: dict, d: design.Design, sheets) -> str:
    t, pn = d.totals, cfg["panel"]
    lines = [f"# Design check - {cfg['project']['name']}", "",
             f"- DC capacity: **{t['dc_kwp']:,.2f} kWp** ({t['panels']} x {pn['pmax']} W)",
             f"- Strings: {t['strings']} x {cfg['design']['modules_per_string']} modules",
             f"- Inverter: {cfg['inverter']['count']} x {cfg['inverter']['brand']} {cfg['inverter']['model']}", "",
             "| Result | Check | Detail |", "|---|---|---|"]
    lines += [f"| {'OK' if c.ok else '**FAIL**'} | {c.name} | {c.detail} |" for c in d.checks]
    lines += ["", "## MPPT assignment", ""]
    for k, inp in enumerate(d.mppt, start=1):
        lines.append(f"- MPPT {k}: " + ", ".join(f"ST {s}" if s else "spare" for s in inp))
    lines += ["", "## Sheets", ""]
    lines += [f"{i + 1}. {s.code or '-'} {s.title}" for i, s in enumerate(sheets)]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("ezdxf").setLevel(logging.ERROR)

    cfg = yaml.safe_load(args.project.read_text(encoding="utf-8"))
    # set the SHX font folder before anything measures text (ezdxf caches fonts on first use)
    fonts_dir = (args.project.parent / cfg["fonts_dir"]).resolve() if cfg.get("fonts_dir") else None
    if fonts_dir and fonts_dir.is_dir():
        ezdxf.options.support_dirs = [str(fonts_dir)]
    dwg = (args.project.parent / cfg["template_dwg"]).resolve()
    out_dir = Path(__file__).parent / "out" / cfg["output_name"]
    out_dir.mkdir(parents=True, exist_ok=True)

    d = design.build(cfg)
    slim, placement = template.frame_template(dwg)
    doc = ezdxf.readfile(slim)
    drawing.setup(doc)
    template.fill_title_block(doc, cfg)

    # owner logo (pulled from the template's OLE picture) - copied next to the DXF like the photo
    logo_def = None
    if placement.get("logo"):
        src_logo = template.CACHE / placement["logo"]
        shutil.copyfile(src_logo, out_dir / "logo.png")
        with Image.open(src_logo) as im:
            logo_def = doc.add_image_def(filename="logo.png", size_in_pixel=im.size)
    placement["logo_def"] = logo_def

    # sheet list: cover, index, site plan, roof plans, single-line diagram, string layouts
    S = drawing.Sheet
    sheets = [S(doc, 0, "", "ปก"), S(doc, 1, "G01", "สารบัญแบบ")]
    site = None
    if cfg.get("site_plan"):
        # photo is copied next to the DXF and referenced by a relative path (CAD resolves it from the drawing folder)
        src_img = (args.project.parent / cfg["site_plan"]["image"]).resolve()
        shutil.copyfile(src_img, out_dir / src_img.name)
        with Image.open(src_img) as im:
            site = (src_img.name, im.size)
        sheets.append(S(doc, len(sheets), "C01", f"ผังรวมอาคาร {cfg['project']['name']}"))
    roof_sheets = []
    for r in d.roofs:
        roof_sheets.append(S(doc, len(sheets), f"C{len(sheets) - 1:02d}", f"ผังการติดตั้งแผงเซลล์แสงอาทิตย์{r.name}"))
        sheets.append(roof_sheets[-1])
    sld = S(doc, len(sheets), "E01", "ไดอะแกรมเส้นเดียวของระบบผลิตไฟฟ้าพลังงานแสงอาทิตย์")
    sheets.append(sld)
    string_sheets = []
    for i, r in enumerate(d.roofs):
        string_sheets.append(S(doc, len(sheets), f"E{i + 2:02d}", f"ผังการเดินสายสตริง {r.name}"))
        sheets.append(string_sheets[-1])

    drawing.cover(sheets[0], cfg, d, logo_def)
    drawing.index_sheet(sheets[1], cfg, sheets, placement, d)
    if site:
        drawing.site_plan_sheet(sheets[2], cfg, d, placement, *site)
    for i, sh in enumerate(roof_sheets):
        drawing.roof_sheet(sh, cfg, d, i, placement)
    drawing.sld_sheet(sld, cfg, d, placement)
    for i, sh in enumerate(string_sheets):
        drawing.string_sheet(sh, cfg, d, i, placement)

    for i, s in enumerate(sheets):
        s.layout(f"{i:02d} {s.code or 'COVER'}")
    if "Layout1" in doc.layouts:
        doc.layouts.delete("Layout1")

    name = cfg["output_name"]
    dxf_path = out_dir / f"{name}.dxf"
    doc.saveas(dxf_path)
    (out_dir / "check_report.md").write_text(report(cfg, d, sheets), encoding="utf-8")

    fails = [c for c in d.checks if not c.ok]
    print(f"DXF     {dxf_path}")
    print(f"Report  {out_dir / 'check_report.md'}")
    print(f"Checks  {len(d.checks) - len(fails)}/{len(d.checks)} OK" + "".join(f"\n  FAIL {c.name}: {c.detail}" for c in fails))

    # SHX fonts: copy the ones the drawing uses next to it (CAD searches the drawing folder too)
    if fonts_dir and fonts_dir.is_dir():
        available = {f.name.lower(): f for f in fonts_dir.iterdir() if f.suffix.lower() == ".shx"}
        wanted = {str(st.dxf.font).lower() for st in doc.styles if str(st.dxf.font).lower().endswith(".shx")}
        for fname in sorted(wanted):
            if fname in available:
                shutil.copyfile(available[fname], out_dir / available[fname].name)
        missing = sorted(w for w in wanted if w not in available)
        if missing:
            print(f"Fonts   not in {fonts_dir.name}/: {', '.join(missing)}")

    if not args.no_plot:
        import plot
        pdf = plot.plot_sheets(dxf_path, [(s.x0, s.y0) for s in sheets], out_dir / f"{name}.pdf", out_dir,
                               fonts_dir=fonts_dir)
        print(f"PDF     {pdf}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
