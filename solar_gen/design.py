"""PV design calculations: panel packing, string/MPPT assignment, electrical checks.

Pure Python (no CAD dependency) so it can be tested and reused by the drawing code.
All lengths are in millimetres.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Panel:
    roof: int          # roof index
    row: int
    col: int
    x: float           # lower-left corner, mm, relative to the roof's lower-left corner
    y: float
    w: float
    h: float
    string: int = 0    # 1-based string number (0 = not assigned)
    pos: int = 0       # 1-based position of the panel inside its string


@dataclass
class RoofLayout:
    index: int
    name: str
    width: float
    depth: float
    setback: float
    panels: list[Panel] = field(default_factory=list)
    capacity: int = 0  # how many panels would fit
    cols: int = 0
    rows: int = 0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Design:
    roofs: list[RoofLayout]
    strings: dict[int, list[Panel]]      # string number -> panels in wiring order
    mppt: list[list[int]]                # per MPPT input: string numbers (0 = spare)
    checks: list[Check]
    totals: dict


def pack_roof(index: int, roof: dict, panel: dict) -> RoofLayout:
    """Place the requested number of panels on the roof.

    racks: horizontal (default) - rows of panels along the roof width, walkway after every N rows.
    racks: vertical - racks run up the sheet (like the PEA example: landscape panels, N across
    per rack, walkway between racks). Done by packing on the transposed roof and swapping x/y back.
    The used block of panels is centred inside the usable area (roof minus setback).
    """
    if roof.get("racks", "horizontal") == "vertical":
        flipped = {**roof, "width": roof["depth"], "depth": roof["width"], "racks": "horizontal",
                   "orientation": "portrait" if roof.get("orientation", "landscape") == "landscape" else "landscape"}
        t = pack_roof(index, flipped, panel)
        # transposed rows become rack columns; keep row = column index so strings run along each rack
        layout = RoofLayout(index, roof["name"], roof["width"], roof["depth"], t.setback,
                            capacity=t.capacity, cols=t.cols, rows=t.rows)
        for p in t.panels:
            layout.panels.append(Panel(index, p.row, p.col, roof["width"] - p.y - p.h, p.x, p.h, p.w))
        layout.panels.sort(key=lambda p: (p.row, p.col))
        return layout
    portrait = roof.get("orientation", "portrait") == "portrait"
    pw, ph = (panel["width"], panel["length"]) if portrait else (panel["length"], panel["width"])
    gap = roof.get("gap", 20)
    setback = roof.get("setback", 1000)
    walk = roof.get("walkway") or {}
    walk_every, walk_w = walk.get("every_rows", 0), walk.get("width", 0)

    usable_w = roof["width"] - 2 * setback
    usable_d = roof["depth"] - 2 * setback
    cols = max(0, int((usable_w + gap) // (pw + gap)))

    # row y-offsets from the top of the usable area, honouring walkways
    row_tops, y = [], 0.0
    while y + ph <= usable_d + 1e-6:
        row_tops.append(y)
        n = len(row_tops)
        y += ph + (walk_w if walk_every and n % walk_every == 0 else gap)
    layout = RoofLayout(index, roof["name"], roof["width"], roof["depth"], setback,
                        capacity=cols * len(row_tops), cols=cols)

    want = min(roof["panels"], layout.capacity)
    rows = math.ceil(want / cols) if cols else 0
    layout.rows = rows
    if not rows:
        return layout

    block_w = cols * pw + (cols - 1) * gap
    block_h = row_tops[rows - 1] + ph
    x0 = setback + (usable_w - block_w) / 2
    top = roof["depth"] - setback - (usable_d - block_h) / 2

    left = want
    for r in range(rows):
        n_in_row = min(cols, left)
        left -= n_in_row
        # centre a short last row
        shift = (cols - n_in_row) * (pw + gap) / 2
        for c in range(n_in_row):
            layout.panels.append(Panel(index, r, c, x0 + shift + c * (pw + gap), top - row_tops[r] - ph, pw, ph))
    return layout


def assign_strings(roofs: list[RoofLayout], per_string: int) -> dict[int, list[Panel]]:
    """Number strings roof by roof, snaking through the rows so each string is contiguous."""
    strings: dict[int, list[Panel]] = {}
    s = 0
    for roof in roofs:
        ordered = []
        for r in range(roof.rows):
            row = sorted((p for p in roof.panels if p.row == r), key=lambda p: p.col, reverse=bool(r % 2))
            ordered.extend(row)
        for i, p in enumerate(ordered):
            if i % per_string == 0:
                s += 1
                strings[s] = []
            p.string, p.pos = s, len(strings[s]) + 1
            strings[s].append(p)
    return strings


def assign_mppt(n_strings: int, inverter: dict) -> list[list[int]]:
    """Spread strings evenly over the MPPTs (as in the PEA example); unused inputs are spare (0).

    15 strings on 10 MPPT x 2 -> MPPT 1-5 take two strings, MPPT 6-10 one each.
    """
    per, m = inverter["strings_per_mppt"], inverter["mppt_count"]
    n = min(n_strings, m * per)
    base, extra = divmod(n, m)
    inputs, s = [], 0
    for k in range(m):
        take = min(per, base + (1 if k < extra else 0))
        inputs.append([s + j + 1 for j in range(take)] + [0] * (per - take))
        s += take
    return inputs


def standard_fuse(current: float, sizes) -> float | None:
    return next((s for s in sorted(sizes) if s >= current), None)


def build(cfg: dict) -> Design:
    panel, inv, d = cfg["panel"], cfg["inverter"], cfg["design"]
    n = d["modules_per_string"]
    roofs = [pack_roof(i, r, panel) for i, r in enumerate(cfg["roofs"])]
    strings = assign_strings(roofs, n)
    mppt = assign_mppt(len(strings), inv)

    n_panels = sum(len(r.panels) for r in roofs)
    dc_kwp = n_panels * panel["pmax"] / 1000
    voc_string = n * panel["voc"]
    voc_max = voc_string * d["voc_factor"]
    vmp_string = n * panel["vmp"]
    isc_design = panel["isc"] * d["isc_factor"]
    fuse = standard_fuse(isc_design, d.get("fuse_sizes", [10, 12, 15, 16, 20, 25, 30, 32]))
    mppt_current = inv["strings_per_mppt"] * panel["isc"]

    checks: list[Check] = []
    for roof, spec in zip(roofs, cfg["roofs"]):
        placed = len(roof.panels)
        checks.append(Check(f"Roof fit: {roof.name}", placed == spec["panels"],
                            f"{placed}/{spec['panels']} panels placed, capacity {roof.capacity}"))
    short = [s for s, ps in strings.items() if len(ps) != n]
    checks.append(Check("Complete strings", not short,
                        f"{len(strings)} strings x {n} modules" + (f"; incomplete: {short}" if short else "")))
    checks.append(Check("Max DC voltage", voc_max <= inv["vdc_max"],
                        f"Voc {n} x {panel['voc']:.2f} x {d['voc_factor']} = {voc_max:.2f} V <= {inv['vdc_max']} V"))
    checks.append(Check("MPPT voltage range", inv["mppt_min"] <= vmp_string <= inv["mppt_max"],
                        f"Vmp {n} x {panel['vmp']:.2f} = {vmp_string:.2f} V in {inv['mppt_min']}-{inv['mppt_max']} V"))
    checks.append(Check("MPPT input current", mppt_current <= inv["imax_mppt"],
                        f"{inv['strings_per_mppt']} x Isc {panel['isc']:.2f} = {mppt_current:.2f} A <= {inv['imax_mppt']} A"))
    checks.append(Check("String inputs", len(strings) <= inv["mppt_count"] * inv["strings_per_mppt"],
                        f"{len(strings)} strings <= {inv['mppt_count'] * inv['strings_per_mppt']} inputs"))
    checks.append(Check("DC fuse", fuse is not None,
                        f"Isc {panel['isc']:.2f} x {d['isc_factor']} = {isc_design:.2f} A -> {fuse} A fuse"))
    ratio = dc_kwp / inv["pac_kw"]
    lo, hi = d.get("dc_ac_ratio", [0.9, 1.35])
    checks.append(Check("DC/AC ratio", lo <= ratio <= hi, f"{dc_kwp:.2f} kWp / {inv['pac_kw']} kW = {ratio:.2f} ({lo}-{hi})"))

    totals = dict(panels=n_panels, strings=len(strings), dc_kwp=dc_kwp, voc_string=voc_string, voc_max=voc_max,
                  vmp_string=vmp_string, isc_design=isc_design, fuse=fuse, ratio=ratio,
                  string_wp=n * panel["pmax"])
    return Design(roofs, strings, mppt, checks, totals)
