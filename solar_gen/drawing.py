"""Sheet drawing for the PEA solar-rooftop set (A3 frames in model space, 1 unit = 1 mm on paper).

Every sheet is placed on a regular grid in model space and gets its own paper-space layout.
Positions of the caption, title-block cells and description box follow the example set.
"""
from __future__ import annotations

import math

from ezdxf.enums import TextEntityAlignment as A

from design import Design
from template import FRAME_BLOCK

PAPER_W, PAPER_H = 420.0, 297.0
PITCH = 460.0                       # spacing between sheets in model space
SCALES = [20, 25, 50, 75, 100, 125, 150, 175, 200, 250, 300, 400, 500, 750, 1000]
STRING_COLORS = [1, 3, 5, 6, 30, 4, 140, 200, 232, 94, 174, 12, 52, 112, 150, 210, 40, 250, 22, 162]
FONT = "Cordia TCR"
WHITE = (255, 255, 255)

LAYERS = {
    "PV": 170, "STR": 7, "STR NUM": 7, "กรอบหลังคา": 1, "A-DIMS-0050": 8, "text": 7, "TITLE": 7,
    "HEAD": 1, "SLD": 7, "INV": 1, "LEGEND": 7, "กรอบPEA": 7, "SITE-IMAGE": 7,
}


def setup(doc) -> None:
    for name, color in LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color)
    if FONT not in doc.styles:
        doc.styles.add(FONT, font="cordia.shx")


def fmt_int(n: float) -> str:
    return f"{n:,.0f}"


class Sheet:
    """Drawing helper working in paper millimetres relative to the frame's lower-left corner."""

    def __init__(self, doc, index: int, code: str, title: str, scale: str = "NTS"):
        self.doc, self.msp = doc, doc.modelspace()
        self.index, self.code, self.title, self.scale = index, code, title, scale
        self.x0, self.y0 = index * PITCH, 0.0

    # --- primitives ---
    def p(self, x, y):
        return (self.x0 + x, self.y0 + y)

    def text(self, s, x, y, h=1.6, align=A.LEFT, layer="text", color=None, rot=0.0, style=FONT):
        attrs = {"layer": layer, "style": style, "rotation": rot}
        if color is not None:
            attrs["color"] = color
        return self.msp.add_text(str(s), height=h, dxfattribs=attrs).set_placement(self.p(x, y), align=align)

    def line(self, x1, y1, x2, y2, layer="SLD", color=None, **kw):
        attrs = {"layer": layer, **kw}
        if color is not None:
            attrs["color"] = color
        return self.msp.add_line(self.p(x1, y1), self.p(x2, y2), dxfattribs=attrs)

    def poly(self, pts, close=False, layer="SLD", color=None, **kw):
        attrs = {"layer": layer, **kw}
        if color is not None:
            attrs["color"] = color
        return self.msp.add_lwpolyline([self.p(x, y) for x, y in pts], close=close, dxfattribs=attrs)

    def rect(self, x1, y1, x2, y2, **kw):
        return self.poly([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], close=True, **kw)

    def circle(self, x, y, r, layer="SLD", color=None):
        attrs = {"layer": layer}
        if color is not None:
            attrs["color"] = color
        return self.msp.add_circle(self.p(x, y), r, dxfattribs=attrs)

    def solid(self, pts, color, layer="SLD", rgb=None):
        h = self.msp.add_hatch(color=color, dxfattribs={"layer": layer})
        if rgb is not None:
            h.rgb = rgb
        h.paths.add_polyline_path([self.p(x, y) for x, y in pts], is_closed=True)
        return h

    def dim(self, p1, p2, base, angle=0.0, factor=1.0, metres=False):
        over = {"dimlfac": factor, "dimtxt": 2.0, "dimasz": 1.5, "dimexo": 1.0, "dimexe": 1.0, "dimdec": 0,
                "dimgap": 0.6, "dimtad": 1, "dimtsz": 0, "dimclrt": 7, "dimclrd": 8, "dimclre": 8}
        if metres:   # PEA roof plans: metres, 2 decimals, tick marks, green extension lines
            over.update({"dimlfac": factor / 1000.0, "dimdec": 2, "dimtsz": 0.9, "dimtxt": 1.8,
                         "dimclre": 3, "dimclrd": 8, "dimzin": 0})
        d = self.msp.add_linear_dim(base=self.p(*base), p1=self.p(*p1), p2=self.p(*p2), angle=angle,
                                    dimstyle="EZDXF", override=over, dxfattribs={"layer": "A-DIMS-0050"})
        d.render()

    def image(self, image_def, x, y, w, h, layer="LEGEND"):
        return self.msp.add_image(insert=self.p(x, y), size_in_units=(w, h), image_def=image_def,
                                  rotation=0, dxfattribs={"layer": layer})

    def block(self, name, x, y, scale=1.0, layer="SLD", rotation=0.0):
        return self.msp.add_blockref(name, self.p(x, y), dxfattribs={
            "layer": layer, "xscale": scale, "yscale": scale, "zscale": scale, "rotation": rotation})

    # --- frame and title ---
    def frame(self, placement: dict, title_block: bool = True):
        if title_block:
            self.msp.add_blockref(FRAME_BLOCK, (self.x0 + placement["dx"], self.y0 + placement["dy"]),
                                  dxfattribs={"layer": "กรอบPEA", "xscale": placement["scale"],
                                              "yscale": placement["scale"], "zscale": placement["scale"]})
            if placement.get("logo_def") is not None:   # owner logo, left side of the OWNER cell
                self.image(placement["logo_def"], 301.0, 35.0, 10.5, 10.5)
            # title-block cells for this sheet
            self.text(self.title, 352.2, 21.58, h=1.35, align=A.MIDDLE_CENTER, layer="TITLE", color=1)
            for ch, x in zip(self.code.ljust(3)[:3], (389.28, 393.68, 398.07)):
                self.text(ch, x, 15.11, h=1.55, align=A.MIDDLE_CENTER, layer="HEAD", color=1)
            self.text("1", 401.87, 16.70, h=1.54, align=A.MIDDLE_CENTER, layer="HEAD", color=1)
            self.text("1", 401.87, 13.46, h=1.54, align=A.MIDDLE_CENTER, layer="HEAD", color=1)
            # caption under the drawing
            half = max(35.2, len(self.title) * 0.55)
            self.text(self.title, 187.98, 39.15, h=1.8, align=A.MIDDLE_CENTER, layer="TITLE", color=1)
            self.line(187.98 - half, 37.2, 187.98 + half, 37.2, layer="TITLE")
            self.text("SCALE", 187.98 - half, 34.17, h=1.5, align=A.MIDDLE_LEFT, layer="TITLE")
            self.text(self.scale, 187.98 + half, 34.17, h=1.5, align=A.MIDDLE_RIGHT, layer="TITLE")
        else:
            self.rect(0, 0, PAPER_W, PAPER_H, layer="กรอบPEA", color=8)
            self.rect(10, 10, PAPER_W - 10, PAPER_H - 10, layer="กรอบPEA", const_width=0.8)

    def layout(self, name: str):
        lay = self.doc.layouts.new(name)
        lay.page_setup(size=(PAPER_W, PAPER_H), margins=(0, 0, 0, 0), units="mm")
        lay.add_viewport(center=(PAPER_W / 2, PAPER_H / 2), size=(PAPER_W, PAPER_H),
                         view_center_point=self.p(PAPER_W / 2, PAPER_H / 2), view_height=PAPER_H)


# ---------------------------------------------------------------- sheets

def cover(sh: Sheet, cfg: dict, d: Design, logo_def=None):
    p = cfg["project"]
    sh.frame({}, title_block=False)
    if logo_def is not None:
        sh.image(logo_def, 210 - 27.5, 180, 55, 55)
    y = 160
    for line in p["programme"]:
        sh.text(line, 210, y, h=5.0, align=A.MIDDLE_CENTER, layer="TITLE")
        y -= 11
    sh.text(p["name"], 210, y - 4, h=4.2, align=A.MIDDLE_CENTER, layer="TITLE", color=1)
    sh.text(f"กำลังการผลิตติดตั้ง {d.totals['dc_kwp']:,.1f} กิโลวัตต์สูงสุด", 210, y - 16, h=4.2,
            align=A.MIDDLE_CENTER, layer="TITLE", color=1)
    sh.text(f"GPS : {p['gps'][0]}, {p['gps'][1]}", 210, y - 28, h=3.2, align=A.MIDDLE_CENTER, layer="TITLE")
    sh.text("แบบระบบผลิตไฟฟ้าพลังงานแสงอาทิตย์", 210, 60, h=7.0, align=A.MIDDLE_CENTER, layer="TITLE")


def index_sheet(sh: Sheet, cfg: dict, sheets: list[Sheet], placement: dict, d: Design | None = None):
    sh.frame(placement)
    if d is not None:   # design check summary under the sheet list
        y = 255 - (len(sheets) + 1) * 8.0 - 18
        sh.text("DESIGN CHECK", 70, y, h=2.6, layer="text")
        sh.line(70, y - 1.8, 100, y - 1.8, layer="text")
        y -= 7
        for c in d.checks:
            sh.text("OK" if c.ok else "FAIL", 72, y, h=1.8, color=3 if c.ok else 1)
            sh.text(c.name, 84, y, h=1.8)
            sh.text(c.detail, 150, y, h=1.8)
            y -= 4.2
    x = [70, 90, 115, 330, 360]
    top, row = 255, 8.0
    sh.text("สารบัญแบบ", 210, 268, h=4.0, align=A.MIDDLE_CENTER, layer="TITLE")
    heads = ["ลำดับ", "รหัสแบบ", "รายการแบบ", "แผ่นที่"]
    rows = [(str(i + 1), s.code or "-", s.title, f"{i + 1}/{len(sheets)}") for i, s in enumerate(sheets)]
    n = len(rows) + 1
    for i in range(n + 1):
        sh.line(x[0], top - i * row, x[-1], top - i * row, layer="text", lineweight=35 if i in (0, 1, n) else -1)
    for xx in x:
        sh.line(xx, top, xx, top - n * row, layer="text")
    for j, hd in enumerate(heads):
        sh.text(hd, (x[j] + x[j + 1]) / 2, top - row / 2, h=2.2, align=A.MIDDLE_CENTER, layer="text")
    for i, r in enumerate(rows, start=1):
        yc = top - i * row - row / 2
        for j, val in enumerate(r):
            al = A.MIDDLE_LEFT if j == 2 else A.MIDDLE_CENTER
            xc = x[j] + 2 if j == 2 else (x[j] + x[j + 1]) / 2
            sh.text(val, xc, yc, h=2.0, align=al, layer="text")


def description_box(sh: Sheet, cfg: dict, d: Design, extra: list[tuple[str, str]] = ()):
    p, inv, pn = cfg["project"], cfg["inverter"], cfg["panel"]
    t = d.totals
    sh.rect(22.2, 12.0, 145, 80, layer="text")
    sh.text("PROJECT DESCRIPTION", 27, 74.5, h=2.0)
    sh.line(27, 73, 69, 73, layer="text")
    sh.text(f"PROJECT : {p['name']}", 27, 68, h=1.5)
    sh.text(f"LOCATION ( LAT / LONG )  :  {p['gps'][0]}, {p['gps'][1]}", 27, 63.5, h=1.5)
    items = [("DC CAPACITY", f"{t['dc_kwp']:,.1f}", "kWp"), ("NO OF MODULE", t["panels"], "MODULES"),
             ("NO OF INVERTER", inv["count"], "SETS"), ("NO OF STRING", t["strings"], "STRINGS"),
             ("MODULE POWER", pn["pmax"], "Wp"), ("INVERTER AC POWER", inv["pac_kw"], "kW"),
             ("MODULE PER STRING", cfg["design"]["modules_per_string"], "PCS/STRING")]
    items += [(a, b, "") for a, b in extra]
    y = 57
    for label, val, unit in items:
        sh.text(label, 27, y, h=1.5)
        sh.text(":", 68, y, h=1.5)
        sh.text(val, 73, y, h=1.5, color=1)
        sh.text(unit, 98, y, h=1.5, color=1)
        y -= 4.8


def string_sheet(sh: Sheet, cfg: dict, d: Design, roof_i: int, placement: dict):
    """String layout: panels numbered and coloured by string (wiring plan)."""
    roof = d.roofs[roof_i]
    spec = cfg["roofs"][roof_i]
    pn = cfg["panel"]
    area_w, area_h, cx, cy = 280.0, 150.0, 172.0, 178.0
    scale = next((s for s in SCALES if roof.width / s <= area_w and roof.depth / s <= area_h), SCALES[-1])
    sh.scale = f"1:{scale}"
    sh.frame(placement)
    u = lambda mm: mm / scale
    rx0, ry0 = cx - u(roof.width) / 2, cy - u(roof.depth) / 2
    rx1, ry1 = rx0 + u(roof.width), ry0 + u(roof.depth)

    # roof outline + name
    sh.rect(rx0, ry0, rx1, ry1, layer="กรอบหลังคา", const_width=0.35)
    sh.text(roof.name, rx0, ry1 + 13, h=2.8, layer="กรอบหลังคา")
    sh.line(rx0, ry1 + 12, rx0 + len(roof.name) * 1.5, ry1 + 12, layer="กรอบหลังคา")
    # setback line
    sb = u(roof.setback)
    sh.rect(rx0 + sb, ry0 + sb, rx1 - sb, ry1 - sb, layer="กรอบหลังคา", color=8, linetype="DASHED", ltscale=0.15)

    # panels coloured by string
    strings_here = sorted({p.string for p in roof.panels})
    th = min(2.2, u(min(pn["width"], pn["length"])) * 0.42)
    for p in roof.panels:
        color = STRING_COLORS[(p.string - 1) % len(STRING_COLORS)]
        x0, y0 = rx0 + u(p.x), ry0 + u(p.y)
        x1, y1 = x0 + u(p.w), y0 + u(p.h)
        sh.rect(x0, y0, x1, y1, layer="PV", color=color)
        sh.text(p.string, (x0 + x1) / 2, (y0 + y1) / 2, h=th, align=A.MIDDLE_CENTER, layer="STR NUM", color=color)
    # string start (+) / end (-) markers
    for s in strings_here:
        ps = d.strings[s]
        for p, mark in ((ps[0], "+"), (ps[-1], "-")):
            x0, y0 = rx0 + u(p.x), ry0 + u(p.y)
            sh.text(mark, x0 + u(p.w) * 0.85, y0 + u(p.h) * 0.85, h=th * 0.8, align=A.MIDDLE_CENTER,
                    layer="STR NUM", color=STRING_COLORS[(s - 1) % len(STRING_COLORS)])

    # dimensions in mm
    if roof.panels:
        bx0 = rx0 + u(min(p.x for p in roof.panels))
        bx1 = rx0 + u(max(p.x + p.w for p in roof.panels))
        by0 = ry0 + u(min(p.y for p in roof.panels))
        by1 = ry0 + u(max(p.y + p.h for p in roof.panels))
        sh.dim((rx0, ry0), (bx0, ry0), (cx, ry0 - 7), factor=scale)
        sh.dim((bx0, ry0), (bx1, ry0), (cx, ry0 - 7), factor=scale)
        sh.dim((bx1, ry0), (rx1, ry0), (cx, ry0 - 7), factor=scale)
        sh.dim((rx1, ry0), (rx1, by0), (rx1 + 7, cy), angle=90, factor=scale)
        sh.dim((rx1, by0), (rx1, by1), (rx1 + 7, cy), angle=90, factor=scale)
        sh.dim((rx1, by1), (rx1, ry1), (rx1 + 7, cy), angle=90, factor=scale)
    sh.dim((rx0, ry0), (rx1, ry0), (cx, ry0 - 14), factor=scale)
    sh.dim((rx0, ry0), (rx0, ry1), (rx0 - 8, cy), angle=90, factor=scale)

    # legend (right column)
    lx, ly = 335.0, 272.0
    compass(sh, 395, 262, _north(cfg, spec))
    sh.text("STRING LEGEND", lx, ly, h=2.0, layer="LEGEND")
    sh.line(lx, ly - 1.5, lx + 28, ly - 1.5, layer="LEGEND")
    y = ly - 7
    for s in strings_here:
        color = STRING_COLORS[(s - 1) % len(STRING_COLORS)]
        sh.rect(lx, y - 1.2, lx + 4, y + 1.2, layer="LEGEND", color=color)
        k = next(i for i, inp in enumerate(d.mppt) if s in inp) + 1
        sh.text(f"ST {s} : {len(d.strings[s])} x {pn['pmax']} W = {fmt_int(len(d.strings[s]) * pn['pmax'])} Wp  (MPPT {k})",
                lx + 6, y, h=1.5, align=A.MIDDLE_LEFT, layer="LEGEND")
        y -= 4.0
    y -= 3
    sh.rect(lx, y - 2, lx + 3, y + 2, layer="PV")
    sh.text(f"PV MODULE {pn['brand']} {pn['pmax']} Wp", lx + 6, y + 0.8, h=1.5, align=A.MIDDLE_LEFT, layer="LEGEND")
    sh.text(f"{pn['width']} x {pn['length']} x {pn['thickness']} mm", lx + 6, y - 1.6, h=1.3,
            align=A.MIDDLE_LEFT, layer="LEGEND")
    y -= 7
    sh.text("+ / -  จุดเริ่ม / จุดสิ้นสุดสตริง", lx, y, h=1.5, align=A.MIDDLE_LEFT, layer="LEGEND")
    y -= 4.5
    sh.line(lx, y, lx + 4, y, layer="กรอบหลังคา", color=8, linetype="DASHED", ltscale=0.15)
    sh.text(f"ระยะร่นขอบหลังคา {fmt_int(roof.setback)} mm", lx + 6, y, h=1.5, align=A.MIDDLE_LEFT, layer="LEGEND")

    n_here = len(roof.panels)
    description_box(sh, cfg, d, extra=[(f"THIS ROOF", f"{n_here} MODULES / {n_here * pn['pmax'] / 1000:,.2f} kWp")])


def _north(cfg: dict, spec: dict | None = None) -> float:
    return (spec or {}).get("north_deg", cfg.get("site_plan", {}).get("north_deg", 90))


def compass(sh: Sheet, x, y, deg=90.0, r=5.0):
    """Four-point compass like the PEA sheets: N/E/S/W around a star, N pointing to `deg`."""
    sh.circle(x, y, r, layer="LEGEND")
    for letter, off in (("N", 0), ("E", -90), ("S", 180), ("W", 90)):
        a = math.radians(deg + off)
        tip = (x + r * 1.05 * math.cos(a), y + r * 1.05 * math.sin(a))
        s1 = (x + r * 0.22 * math.cos(a + math.pi / 2), y + r * 0.22 * math.sin(a + math.pi / 2))
        s2 = (x + r * 0.22 * math.cos(a - math.pi / 2), y + r * 0.22 * math.sin(a - math.pi / 2))
        sh.solid([tip, s1, (x, y)], color=7, layer="LEGEND")
        sh.poly([tip, s2, (x, y)], close=True, layer="LEGEND")
        sh.text(letter, x + r * 1.55 * math.cos(a), y + r * 1.55 * math.sin(a), h=2.2 if letter == "N" else 1.7,
                align=A.MIDDLE_CENTER, layer="LEGEND")


ROOF_SYMBOLS = {   # feature type -> legend text
    "RACEWAY": "RACEWAY", "WATER": "WATER PIPE", "TAP": "WATER TAP", "LADDER": "SERVICE LADDER",
    "WALKWAY": "WALK WAY", "LIFELINE": "LIFELINE", "WEATHER": "WEATHER STATION",
}


def _weather(sh: Sheet, x, y, r=2.6):
    sh.circle(x, y, r, layer="LEGEND", color=1)
    sh.line(x - r * 1.3, y, x + r * 1.3, y, layer="LEGEND", color=1)
    sh.line(x, y, x - r * 0.8, y - r * 0.8, layer="LEGEND", color=1)
    sh.line(x, y, x + r * 0.8, y - r * 0.8, layer="LEGEND", color=1)
    sh.circle(x - r * 1.3, y, r * 0.18, layer="LEGEND", color=1)
    sh.circle(x + r * 1.3, y, r * 0.18, layer="LEGEND", color=1)


def _tap(sh: Sheet, x, y, r=0.9):
    sh.solid([(x + r * math.cos(t / 8 * math.pi), y + r * math.sin(t / 8 * math.pi)) for t in range(16)], color=2,
             layer="LEGEND")
    sh.circle(x, y, r, layer="LEGEND", color=2)
    sh.text("T", x, y, h=r * 1.1, align=A.MIDDLE_CENTER, layer="LEGEND", color=7)


def _legend_symbol(sh: Sheet, kind: str, x, y):
    if kind == "PV":
        sh.rect(x - 3, y - 1.2, x + 3, y + 1.2, layer="PV", color=5)
    elif kind in ("RACEWAY", "WATER", "WALKWAY", "LIFELINE"):
        color = {"RACEWAY": 6, "WATER": 3, "WALKWAY": 1, "LIFELINE": 4}[kind]
        sh.solid([(x - 3, y - 0.6), (x + 3, y - 0.6), (x + 3, y + 0.6), (x - 3, y + 0.6)], color=color, layer="LEGEND")
    elif kind == "TAP":
        _tap(sh, x, y)
    elif kind == "LADDER":
        sh.solid([(x + 1.4 * math.cos((t + 0.5) / 4 * math.pi), y + 1.4 * math.sin((t + 0.5) / 4 * math.pi))
                  for t in range(8)], color=1, layer="LEGEND")
    elif kind == "WEATHER":
        _weather(sh, x, y, r=1.4)


def roof_sheet(sh: Sheet, cfg: dict, d: Design, roof_i: int, placement: dict):
    """Roof installation plan in the style of the PEA example (blue panels, metre dims, roof features)."""
    roof, spec, pn = d.roofs[roof_i], cfg["roofs"][roof_i], cfg["panel"]
    area_w, area_h, cx, cy = 285.0, 165.0, 190.0, 172.0
    scale = next((s for s in SCALES if roof.width / s <= area_w and roof.depth / s <= area_h), SCALES[-1])
    sh.scale = f"1:{scale}"
    sh.frame(placement)
    u = lambda mm: mm / scale
    rx0, ry0 = cx - u(roof.width) / 2, cy - u(roof.depth) / 2
    rx1, ry1 = rx0 + u(roof.width), ry0 + u(roof.depth)
    at = lambda xy: (rx0 + u(xy[0]), ry0 + u(xy[1]))

    sh.rect(rx0, ry0, rx1, ry1, layer="กรอบหลังคา")
    for p in roof.panels:
        x0, y0 = rx0 + u(p.x), ry0 + u(p.y)
        sh.rect(x0, y0, x0 + u(p.w), y0 + u(p.h), layer="PV", color=5)

    used = ["PV"]
    for f in spec.get("features", []):
        kind = f["type"].upper()
        if kind == "VENT":
            x, y = at(f["at"])
            sh.circle(x, y, u(f.get("r", 900)), layer="กรอบหลังคา", color=8)
        elif kind == "HATCH":
            x, y = at(f["at"])
            hs = u(f.get("size", 800)) / 2
            sh.rect(x - hs, y - hs, x + hs, y + hs, layer="กรอบหลังคา", color=8)
        elif kind == "INV":
            x, y = at(f["at"])
            w, h = u(f.get("w", 6000)), u(f.get("h", 2500))
            sh.rect(x, y, x + w, y + h, layer="INV", color=1)
            sh.text("INV", x + w / 2, y + h / 2, h=h * 0.62, align=A.MIDDLE_CENTER, layer="INV", color=1)
        elif kind == "FTS":
            x, y = at(f["at"])
            sh.rect(x - 0.7, y - 1.9, x + 0.7, y + 1.9, layer="INV", color=1)
            sh.text("FTS", x, y, h=1.0, align=A.MIDDLE_CENTER, layer="INV", color=1, rot=90)
            if f.get("label"):
                lx = max(rx1 + 6, x + 25)
                sh.poly([(x + 0.7, y), (lx + 12, y)], layer="LEGEND", const_width=0.5)
                for i, line in enumerate(str(f["label"]).split("/")):
                    sh.text(line.strip(), lx + 12, y + 3.4 - i * 2.4, h=1.5, align=A.MIDDLE_RIGHT, layer="LEGEND")
        elif kind in ("RACEWAY", "WATER", "WALKWAY", "LIFELINE"):
            color = {"RACEWAY": 6, "WATER": 3, "WALKWAY": 1, "LIFELINE": 4}[kind]
            sh.poly([at(xy) for xy in f["path"]], layer="LEGEND", color=color, const_width=0.35)
            for xy in f.get("taps", []):
                _tap(sh, *at(xy))
                if "TAP" not in used:
                    used.append("TAP")
        elif kind == "LADDER":
            x, y = at(f["at"])
            _legend_symbol(sh, "LADDER", x, y)
        elif kind == "WEATHER":
            _weather(sh, *at(f["at"]))
        if kind in ROOF_SYMBOLS and kind not in used:
            used.append(kind)

    # overall dimensions in metres (top and left), as on the example
    sh.dim((rx0, ry1), (rx1, ry1), (cx, ry1 + 9), factor=scale, metres=True)
    sh.dim((rx0, ry0), (rx0, ry1), (rx0 - 9, cy), angle=90, factor=scale, metres=True)

    # roof description (right, right-aligned)
    info = [roof.name, spec.get("roof_type", ""),
            f"ความสูงจากพื้น {spec['height_m']} เมตร" if "height_m" in spec else "",
            f"ความลาดเอียง {spec['slope_deg']} องศา" if "slope_deg" in spec else ""]
    y = 146
    for line in (i for i in info if i):
        sh.text(line, 404, y, h=1.9, align=A.RIGHT, layer="text")
        y -= 4.6

    # solar panel slope box
    sh.rect(364.5, 86.5, 406, 123, layer="text")
    sh.text("SOLAR PANEL SLOPE", 385.2, 117.5, h=2.0, align=A.MIDDLE_CENTER, layer="text")
    ang = math.radians(spec.get("panel_slope_dir", 0))
    for r in range(3):
        for c in range(2):
            x0, y0 = 370.5 + c * 15.5, 88.5 + r * 8.2
            sh.rect(x0, y0, x0 + 15, y0 + 7.6, layer="text")
            sh.text("Slope", x0 + 7.5, y0 + 4.9, h=1.6, align=A.MIDDLE_CENTER, layer="text")
            mx, my = x0 + 7.5, y0 + 2.2
            dx, dy = 5 * math.cos(ang), 5 * math.sin(ang)
            sh.line(mx - dx, my - dy, mx + dx, my + dy, layer="text")
            sh.solid([(mx + dx, my + dy), (mx + dx - 1.2 * math.cos(ang - 0.4), my + dy - 1.2 * math.sin(ang - 0.4)),
                      (mx + dx - 1.2 * math.cos(ang + 0.4), my + dy - 1.2 * math.sin(ang + 0.4))], color=7, layer="text")

    # symbol description + compass
    sh.rect(339.5, 48.5, 406, 79, layer="text")
    sh.text("SYMBOL DESCRIPTION :", 372.7, 75, h=1.9, align=A.MIDDLE_CENTER, layer="text")
    items = [("PV", "PV MODULE")] + [(k, ROOF_SYMBOLS[k]) for k in used if k in ROOF_SYMBOLS]
    for i, (k, label) in enumerate(items):
        col, row = divmod(i, 4)
        x, y = 344 + col * 32, 69.5 - row * 5.6
        _legend_symbol(sh, k, x, y)
        sh.text(label, x + 5, y, h=1.5, align=A.MIDDLE_LEFT, layer="text")
    compass(sh, 325, 64, _north(cfg, spec))

    # roof summary (bottom-left box, like the example)
    n_here = len(roof.panels)
    strings_here = sorted({p.string for p in roof.panels})
    per = cfg["design"]["modules_per_string"]
    sh.rect(22.2, 12.0, 98.8, 55.0, layer="text")
    sh.text(roof.name, 31, 48.6, h=2.4)
    sh.line(31, 46.6, 70, 46.6, layer="text")
    rows = [("DC CAPACITY :", f"{n_here * pn['pmax'] / 1000:,.2f} kWp"), ("", ""),
            ("NO OF MODULE", f"{n_here}    MODULES"), ("NO OF STRING", f"{len(strings_here)}    STRINGS"), ("", ""),
            ("MODULE POWER", f"{pn['pmax']}    Wp"), ("", ""), ("MODULE PER STRING", f"{per}    PCS/STRING")]
    y = 41
    for label, val in rows:
        if label:
            sh.text(label, 31, y, h=1.5)
            sh.text(val, 66, y, h=1.5)
        y -= 3.4


# ---------------------------------------------------------------- single-line diagram (PEA example layout)

def _open_arrow(sh: Sheet, x, y, up=True, w=1.5, h=3.0, layer="SLD"):
    """Hollow triangle arrow head; (x, y) is the base centre."""
    tip = y + h if up else y - h
    sh.poly([(x - w, y), (x + w, y), (x, tip)], close=True, layer=layer)


def _note(sh: Sheet, bar_x, y_mid, lines, color=1, h=1.8, step=3.5):
    """Cable note: vertical bar with the text lines to its right (example style)."""
    n = len(lines)
    top = y_mid + (n - 1) * step / 2
    sh.line(bar_x, top + 1.7, bar_x, top - (n - 1) * step - 1.1, layer="text", color=color)
    for i, s in enumerate(lines):
        sh.text(s, bar_x + 1.4, top - i * step - 0.6, h=h, color=color)


def sld_sheet(sh: Sheet, cfg: dict, d: Design, placement: dict):
    """PV single-line diagram laid out like the example (positions measured from its sheet E03)."""
    sh.frame(placement)
    inv, pn, dz, t = cfg["inverter"], cfg["panel"], cfg["design"], d.totals
    cables = cfg.get("cables", {})
    n = dz["modules_per_string"]
    brand = inv["brand"].upper()

    # --- inverter spec (label : value) ---
    sh.rect(22.2, 232.0, 103.0, 285.1, layer="text")
    sh.line(22.2, 278.4, 103.0, 278.4, layer="text")
    sh.text(f"{brand} GRID TIED SOLAR INVERTER", 27.6, 280.5, h=1.8)
    rows = [(f"{brand} MODEL", f": {inv['model']}"), ("DC INPUT", ""),
            ("- MAX VOLTAGE", f": {fmt_int(inv['vdc_max'])}V"), ("- MAX CURRENT", f": {inv['imax_mppt']}A / 1MPPT"),
            ("- MPPT RANGE", f": {inv['mppt_min']}-{fmt_int(inv['mppt_max'])} V"),
            ("- NUMBER OF MPPT", f": {inv['mppt_count']}"), ("- STRING PER MPPT", f": {inv['strings_per_mppt']}"),
            None, ("AC OUTPUT", ""), ("- MAX POWER", f": {inv['sac_kva']} kVA / {inv['pac_kw']} kW"),
            ("- NOMINAL VOLTAGE", f": {inv['ac_voltage']}"), ("- MAX CURRENT", f": {inv['iac_max']}A"),
            ("- POWER FACTOR RANGE", f": {inv.get('power_factor', '0.8leading/0.8lagging')}"),
            ("- FREQUENCY", f": {inv.get('frequency', '50Hz / 60Hz')}")]
    y = 273.6
    for r in rows:
        if r is None:
            y -= 1.7
            continue
        sh.text(r[0], 27.6, y, h=1.8)
        if r[1]:
            sh.text(r[1], 62.5, y, h=1.8)
        y -= 3.0

    # --- panel spec ---
    sh.rect(103.0, 232.0, 160.4, 285.1, layer="text")
    sh.line(103.0, 272.5, 160.4, 272.5, layer="text")
    sh.text(f"{pn['brand'].upper()} SOLAR PANEL", 107.8, 279.7, h=1.8)
    sh.text(pn["type"], 107.8, 274.9, h=1.8)
    prow = [("Model", f": {pn['model']}"), ("Pmax", f": {pn['pmax']}W"), ("Vmp", f": {pn['vmp']:.2f}V"),
            ("Imp", f": {pn['imp']:.2f}A"), ("Voc", f": {pn['voc']:.2f}"), ("Isc", f": {pn['isc']:.2f}A"),
            ("Voc Total", f": {t['voc_string']:.1f}V x {dz['voc_factor']:.2f} = {t['voc_max']:.2f}V"),
            ("Isc Total", f": {pn['isc']:.2f}A x {dz['isc_factor']:.2f} = {t['isc_design']:.2f}A")]
    y = 266.8
    for label, val in prow:
        sh.text(label, 107.8, y, h=1.8)
        sh.text(val, 123.9, y, h=1.8)
        y -= 3.75

    # --- MPPT / string table ---
    sh.rect(300.6, 215.0, 408.2, 285.1, layer="text")
    sh.line(300.6, 273.6, 408.2, 273.6, layer="text")
    sh.line(354.2, 273.6, 354.2, 215.0, layer="text")
    sh.line(300.6, 221.0, 408.2, 221.0, layer="text")
    sh.text(f"INVERTER  {inv['brand']} {inv['model']}", 306.3, 280.7, h=1.8)
    sh.text(f"SOLAR PANEL : {pn['brand']} SOLAR {pn['pmax']}W ({pn['model']})", 306.6, 276.2, h=1.8)
    per = inv["strings_per_mppt"]
    half = math.ceil(len(d.mppt) / 2)
    block = min(9.8, 50.0 / half)
    line_step = block / (per + 1)
    for col in range(2):
        xl, xv = (302.8, 320.3) if col == 0 else (355.8, 373.1)
        for j, k in enumerate(range(col * half, min(len(d.mppt), (col + 1) * half))):
            y = 270.7 - j * block
            sh.text(f"MPPT {k + 1}", xl, y, h=1.8)
            for i, s in enumerate(d.mppt[k]):
                y -= line_step
                sh.text(f"-String {k * per + i + 1}", xl, y, h=1.8)
                txt = f": {len(d.strings[s])} Module {fmt_int(len(d.strings[s]) * pn['pmax'])}Wp" if s else ": SPARE"
                sh.text(txt, xv, y, h=1.8, color=1)
    sh.text(f"Total solar cell {t['panels']} panel", 302.8, 217.4, h=1.8, color=1)
    sh.text(f"Total power {fmt_int(t['dc_kwp'] * 1000)}Wp", 355.6, 217.4, h=1.8, color=1)

    # --- inverter box + AC side ---
    ix0, iy0, ix1, iy1 = 160.4, 208.6, 213.2, 227.3
    sh.rect(ix0, iy0, ix1, iy1, layer="INV", color=7)
    sh.line(ix0, iy0, ix1, iy1, layer="INV", color=7)
    sh.text("~", 170.5, 222.0, h=3.2, align=A.MIDDLE_CENTER, layer="INV", color=7)
    sh.line(198.5, 215.2, 206.0, 215.2, layer="INV", color=7)      # DC symbol, clear of the input numbers
    for i in range(3):
        sh.line(198.5 + i * 2.7, 213.9, 200.2 + i * 2.7, 213.9, layer="INV", color=7)
    sh.text("INVERTER No.01", 216.8, 220.9, h=1.8, color=1)
    sh.text(f"{inv['brand']} {inv['model']}", 216.8, 217.5, h=1.8)
    sh.text(inv.get("rating_text", f"{inv['pac_kw']}kW"), 216.8, 214.3, h=1.8)
    ac_x = 186.8
    sh.line(ac_x, iy1, ac_x, 241.0, layer="SLD")
    _open_arrow(sh, ac_x, 241.0, up=True)
    sh.text("SOLAR AC-DB-01", 175.9, 248.0, h=1.8, color=1)
    sh.line(ac_x, 235.6, 215.4, 235.6, layer="SLD")
    ac_note = cables.get("ac", ["CV-FD 0.6/1kV 4x1C-120 Sq.mm. /IEC01 1C-25 Sq.mm.(G)",
                                "RUN IN PERFORATED TRAY 200x100 mm. (WITH COVER)"])
    _note(sh, 215.4, 235.6, ac_note if isinstance(ac_note, list) else [ac_note])

    # --- inverter DC inputs: numbered terminals, DC switch groups ---
    n_inputs = len(d.mppt) * per
    pitch = min(2.52, (ix1 - ix0 - 4) / max(1, n_inputs - 1))
    term_x = [163.5 + i * pitch for i in range(n_inputs)]
    ty = 204.4
    for i, x in enumerate(term_x):   # numbers inside the box, terminals below it (DC SW labels sit between)
        sh.text(i + 1, x, 210.4, h=1.3, align=A.MIDDLE_CENTER, layer="INV")
        sh.circle(x, ty, 0.8)
    sh.line(term_x[-1] + 0.8, ty, 215.4, ty, layer="SLD")
    sh.text(cables.get("dc_inverter", "PV Cable H1Z2Z2-K 2X1C-6 Sq.mm."), 216.8, 203.3, h=1.8)
    for k, (a, b) in enumerate(inv.get("dc_switch_groups", [[1, n_inputs]])):
        sh.text(f"DC SW {k + 1}", term_x[a - 1] - 0.6, 206.0, h=1.6, color=1)
    term_of = {}
    for k, inp in enumerate(d.mppt):
        for j, s in enumerate(inp):
            if s:
                term_of[s] = term_x[k * per + j]

    # --- string columns ---
    ns = len(d.strings)
    spitch = min(14.3, (330.0 - 50.9) / max(1, ns - 1))
    sx = {s: 50.9 + i * spitch for i, s in enumerate(sorted(d.strings))}

    # route terminal -> level -> string column (outer strings run highest so nothing crosses)
    left = sorted((s for s in sx if sx[s] <= term_of.get(s, 0)), key=lambda s: sx[s])
    right = sorted((s for s in sx if s not in left), key=lambda s: -sx[s])
    step = min(1.3, 30.0 / max(1, max(len(left), len(right))))
    level = {}
    for group in (left, right):
        for rank, s in enumerate(group):
            level[s] = 198.0 - rank * step
    for s, x in sx.items():
        tx = term_of.get(s)
        if tx is not None:
            sh.poly([(tx, ty - 0.8), (tx, level[s]), (x, level[s]), (x, 148.66)], layer="SLD")

    # DC combiner (DC-DB): SPD + fuse per string, G bar
    sh.rect(37.0, 137.5, 337.0, 162.4, layer="SLD", color=5, linetype="DASHED", ltscale=0.4)
    sh.text("SOLAR DC-DB-01", 37.5, 164.2, h=1.8, color=1)
    sh.text("G Bar", 39.5, 154.7, h=1.8)
    sh.rect(40.4, 145.6, 42.4, 153.2, layer="SLD")
    for i in range(6):
        sh.line(40.4, 146.6 + i * 1.2, 42.4, 146.6 + i * 1.2, layer="SLD")
    sh.text("DC.Input", 39.6, 141.8, h=1.8)
    sh.poly([(40.4, 149.4), (33.0, 149.4), (33.0, 165.6)], layer="SLD")
    _open_arrow(sh, 33.0, 165.6, up=True)
    sh.text("TO SOLAR AC-DB-01", 34.1, 172.0, h=1.8, rot=90)
    for s, x in sx.items():
        sh.block("DC SPD", x + 4.075, 153.9, scale=0.5)
        sh.block("DC FUSE", x - 0.855, 143.09, scale=0.5)

    # firefighter switch (FTS-DB), groups of N strings
    sh.rect(37.0, 110.1, 337.0, 127.6, layer="SLD", color=5, linetype="DASHED", ltscale=0.4)
    sh.text("SOLAR FTS-DB-01", 37.5, 129.8, h=1.8, color=1)
    g = dz.get("fts_group", 5)
    ordered = sorted(sx)
    for k in range(0, len(ordered), g):
        grp = ordered[k:k + g]
        gx0, gx1 = sx[grp[0]] - 6.3, sx[grp[-1]] + 6.2
        sh.rect(gx0, 111.8, gx1, 121.4, layer="SLD")
        sh.text(f"FTS {k // g + 1:02d}", gx0 + 1.1, 123.9, h=1.8, color=1)
    for s, x in sx.items():
        sh.line(x, 141.77, x, 120.6, layer="SLD")
        sh.line(x - 0.6, 120.0, x + 0.6, 121.2, layer="SLD")       # x mark (fixed contact)
        sh.line(x - 0.6, 121.2, x + 0.6, 120.0, layer="SLD")
        sh.line(x, 115.4, x - 1.4, 119.4, layer="SLD")            # switch blade
        sh.line(x, 115.4, x, 93.02, layer="SLD")

    # equipment ground bus with hops over each string, to the ground box
    hop = 1.1
    xs_sorted = sorted(sx.values())
    x_prev = 32.6
    for x in xs_sorted:
        sh.line(x_prev, 104.0, x - hop, 104.0, layer="SLD")
        sh.msp.add_arc(sh.p(x, 104.0), hop, 0, 180, dxfattribs={"layer": "SLD"})
        x_prev = x + hop
    sh.line(x_prev, 104.0, 339.8, 104.0, layer="SLD")
    sh.line(32.6, 104.0, 32.6, 95.6, layer="SLD")
    _open_arrow(sh, 32.6, 95.6, up=False)
    sh.text("TO GROUND BOX", 33.7, 70.1, h=1.8, rot=90)
    gnote = cables.get("dc", ["PV Cable H1Z2Z2-K 2X1C-6 Sq.mm.", "RUN IN PERFORATED TRAY  100x100 mm.(WITH COVER)",
                              "IEC01 1C-6 Sq.mm.(G)"])
    _note(sh, 339.8, 97.6, gnote if isinstance(gnote, list) else [gnote])

    # emergency switch
    sh.poly([(337.0, 119.0), (350.9, 119.0), (350.9, 168.0)], layer="SLD")
    _open_arrow(sh, 350.9, 168.0, up=True)
    sh.text("EMERGENCY SWITCH", 339.0, 173.0, h=1.8)
    sh.line(350.9, 147.4, 355.0, 147.4, layer="SLD")
    _note(sh, 355.0, 147.4, cables.get("emergency", ["VCT 1-2Cx1.5/1.5(G) Sq.mm.", 'IN IMC CONDUIT %%C3/4"']),
          color=7, step=3.3)

    # PV strings (PV PANEL blocks: 01, 02, 03 ... n)
    for s, x in sx.items():
        for k, y in enumerate((88.19, 82.63, 77.08)):
            sh.block("PV PANEL", x - 1.64, y, scale=0.5)
            sh.text(f"{k + 1:02d}", x + 2.6, y + 2.5, h=1.5, align=A.MIDDLE_LEFT)
        sh.line(x, 82.63 + 4.84, x, 88.19 + 0.2, layer="SLD")
        sh.line(x, 77.08 + 4.84, x, 82.63 + 0.2, layer="SLD")
        for y in (75.3, 74.1, 72.9):
            sh.circle(x, y, 0.22)
        sh.block("PV PANEL", x - 1.64, 69.04, scale=0.5)
        sh.text(f"{n:02d}", x + 2.6, 69.04 + 2.5, h=1.5, align=A.MIDDLE_LEFT)
        sh.text(f"ST {s}", x, 67.3, h=1.8, align=A.MIDDLE_CENTER, color=1)


# ---------------------------------------------------------------- site plan

MARKERS = {   # type -> (circle colour, legend text)
    "TR": (8, "TRANSFORMER EXISTING"), "MDB": (8, "MDB EXISTING"), "METER": (8, "PEA METER"),
    "ZERO": (1, "จุดติดตั้ง Zero Export"), "INV": (1, "ตำแหน่ง Inverter Station"), "WEATHER": (1, "WEATHER STATION"),
}


def _marker(sh: Sheet, kind: str, x: float, y: float, label: str, r: float = 3.6):
    color = MARKERS.get(kind, (7, ""))[0]
    if kind == "INV":
        sh.solid([(x - 4.5, y - 2.2), (x + 4.5, y - 2.2), (x + 4.5, y + 2.2), (x - 4.5, y + 2.2)],
                 color=7, layer="LEGEND", rgb=WHITE)
        sh.rect(x - 4.5, y - 2.2, x + 4.5, y + 2.2, layer="LEGEND", color=1)
        sh.text("INV", x, y, h=2.6, align=A.MIDDLE_CENTER, layer="LEGEND", color=1)
        return
    sh.circle(x, y, r, layer="LEGEND", color=color)
    if kind == "WEATHER":
        sh.line(x - r, y, x + r, y, layer="LEGEND", color=1)
        sh.line(x, y - r * 0.6, x, y + r * 0.6, layer="LEGEND", color=1)
        return
    words = label.split()
    if len(words) > 1 and len(label) > 5:
        sh.text(words[0], x, y + 0.9, h=1.1, align=A.MIDDLE_CENTER, layer="LEGEND", color=color)
        sh.text(" ".join(words[1:]), x, y - 0.9, h=1.1, align=A.MIDDLE_CENTER, layer="LEGEND", color=color)
    else:
        sh.text(label, x, y, h=1.6, align=A.MIDDLE_CENTER, layer="LEGEND", color=color)


def site_plan_sheet(sh: Sheet, cfg: dict, d: Design, placement: dict, image_name: str, image_px: tuple[int, int]):
    site = cfg["site_plan"]
    mpp = site["metres_per_pixel"] * 1000.0          # mm per pixel
    img_w, img_h = image_px[0] * mpp, image_px[1] * mpp
    area_w, area_h = 306.0, 175.0
    scale = next((s for s in SCALES if img_w / s <= area_w and img_h / s <= area_h), SCALES[-1])
    sh.scale = f"1:{scale}"
    ix0, iy1 = 25.0, 92.0 + img_h / scale              # photo top-left on the sheet

    def at(px, py):                                    # photo pixel -> sheet position
        return ix0 + px * mpp / scale, iy1 - py * mpp / scale

    # photo first so everything else draws on top of it
    img_def = sh.doc.add_image_def(filename=image_name, size_in_pixel=image_px)
    sh.msp.add_image(insert=sh.p(ix0, iy1 - img_h / scale), size_in_units=(img_w / scale, img_h / scale),
                     image_def=img_def, rotation=0, dxfattribs={"layer": "SITE-IMAGE"})
    sh.frame(placement)
    sh.rect(ix0, iy1 - img_h / scale, ix0 + img_w / scale, iy1, layer="LEGEND", color=8)

    # roofs with their panels, coloured by string
    for roof, spec in zip(d.roofs, cfg["roofs"]):
        if "site" not in spec:
            continue
        px, py = spec["site"]["px"]
        rx0, ry1 = at(px, py)
        rx1, ry0 = rx0 + roof.width / scale, ry1 - roof.depth / scale
        for p in roof.panels:
            color = STRING_COLORS[(p.string - 1) % len(STRING_COLORS)]
            x0, y0 = rx0 + p.x / scale, ry0 + p.y / scale
            sh.rect(x0, y0, x0 + p.w / scale, y0 + p.h / scale, layer="PV", color=color)
        sh.rect(rx0, ry0, rx1, ry1, layer="กรอบหลังคา", const_width=0.4)
        lx, ly = rx0, ry1 + 4
        sh.rect(lx - 1, ly - 2.2, lx + len(roof.name) * 1.45 + 2, ly + 2.8, layer="กรอบหลังคา")
        sh.solid([(lx - 0.8, ly - 2.0), (lx + len(roof.name) * 1.45 + 1.8, ly - 2.0),
                  (lx + len(roof.name) * 1.45 + 1.8, ly + 2.6), (lx - 0.8, ly + 2.6)], color=7, layer="LEGEND", rgb=WHITE)
        sh.text(roof.name, lx + 0.5, ly + 0.3, h=2.2, align=A.MIDDLE_LEFT, layer="กรอบหลังคา", color=1)

    # equipment markers with white backing so they read over the photo
    for eq in site.get("equipment", []):
        x, y = at(*eq["px"])
        if eq["type"] != "INV":
            sh.solid([(x + 3.6 * math.cos(t / 12 * math.pi), y + 3.6 * math.sin(t / 12 * math.pi)) for t in range(24)],
                     color=7, layer="LEGEND", rgb=WHITE)
        _marker(sh, eq["type"], x, y, eq["label"])
        if eq.get("note"):
            sh.rect(x + 4.5, y - 1.8, x + 6 + len(eq["note"]) * 1.1, y + 1.8, layer="LEGEND", color=8)
            sh.solid([(x + 4.6, y - 1.7), (x + 5.9 + len(eq["note"]) * 1.1, y - 1.7),
                      (x + 5.9 + len(eq["note"]) * 1.1, y + 1.7), (x + 4.6, y + 1.7)], color=7, layer="LEGEND", rgb=WHITE)
            sh.text(eq["note"], x + 5.3, y, h=1.6, align=A.MIDDLE_LEFT, layer="LEGEND", color=8)

    # legend + north arrow (right column)
    lx, y = 337.0, 200.0
    compass(sh, 372, 250, site.get("north_deg", 90))
    sh.text("LEGEND", lx, y + 8, h=2.0, layer="LEGEND")
    sh.line(lx, y + 6.5, lx + 14, y + 6.5, layer="LEGEND")
    sh.rect(lx - 3, y - 2, lx + 3, y + 2, layer="PV")
    sh.text("PV MODULE", lx + 8, y, h=1.7, align=A.MIDDLE_LEFT, layer="LEGEND")
    y -= 7.5
    sh.rect(lx - 3, y - 2, lx + 3, y + 2, layer="กรอบหลังคา", const_width=0.4)
    sh.text("ขอบเขตหลังคาติดตั้ง", lx + 8, y, h=1.7, align=A.MIDDLE_LEFT, layer="LEGEND")
    seen = []
    for eq in site.get("equipment", []):
        if eq["type"] in seen or eq["type"] not in MARKERS:
            continue
        seen.append(eq["type"])
        y -= 8.5
        label = {"TR": "TR X", "MDB": "MDB X"}.get(eq["type"], eq["label"])
        _marker(sh, eq["type"], lx, y, label)
        sh.text(MARKERS[eq["type"]][1], lx + 8, y, h=1.7, align=A.MIDDLE_LEFT, layer="LEGEND")

    description_box(sh, cfg, d)
