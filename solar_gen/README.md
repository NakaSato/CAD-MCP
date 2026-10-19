# solar_gen — PEA solar-rooftop drawing set generator

Builds a drawing set (DXF + PDF) from one project file, using the PEA frame from an example DWG.

```bash
brew install libredwg                                    # once: DWG -> DXF converter (dwg2dxf)
uv run solar_gen/generate.py solar_gen/projects/sample.yaml
```

Output goes to `solar_gen/out/<output_name>/`:

| File | What |
|---|---|
| `<name>.dxf` | Full set, one paper-space layout per sheet. Open in AutoCAD/GstarCAD and *Save As* DWG. |
| `<name>.pdf`, `sheetNN.png` | A3 plot of every sheet, using the SHX fonts from `fonts_dir`. |
| `check_report.md` | Electrical/fit checks and MPPT assignment. Exit code is 1 if any check fails. |

## Sheets generated

Styled after the PEA example set (its frame, logo, symbol blocks and SHX fonts are reused).

| Code | Sheet |
|---|---|
| – | Cover with the owner logo |
| G01 | Index (built from the sheet list) + design-check table |
| C01 | Site plan (when `site_plan` is set): drone photo to scale, roofs with their panels, equipment markers, legend, compass |
| C02… | Roof plan per roof, like the example: blue panels (vertical racks if `racks: vertical`), roof features (vents, hatches, INV, FTS, raceway, water pipe/taps, ladder, weather station), dimensions in metres, roof description, panel-slope box, symbol legend, compass |
| E01 | PV single-line diagram, laid out like the example: inverter/panel spec tables, MPPT/string table, inverter with numbered inputs and DC switch groups, DC-DB (DC SPD + DC FUSE blocks), FTS-DB groups, ground bus, cable notes |
| E02… | String layout per roof: panels numbered and coloured by string, with + / − ends |

## New project

Copy `projects/sample.yaml`, edit the values, run it. Things to know:

- `drawing_no` must stay 15 characters (one per box in the title block).
- Each roof's `panels` should be a multiple of `modules_per_string`, or the check fails.
- Strings are numbered roof by roof, snaking along the rows (or racks).
- `fonts_dir` points at the SHX fonts (`cordia.shx` ...). They are used for the plot and copied next to the DXF.
- The owner logo is taken from the template's title block automatically.
- Strings are spread evenly over the MPPTs (15 strings on 10 MPPT x 2 -> MPPT 1-5 two each, 6-10 one each).
- Roof `features` take positions in mm from the roof's lower-left corner (see `projects/sample.yaml`).
- Site plan: set `metres_per_pixel` for the photo (measure one known length, e.g. a roof edge, in pixels),
  then give each roof's top-left corner as `site.px` and each equipment marker's `px`, in photo pixels from
  the top-left. The photo is copied next to the DXF and referenced by file name, so keep them together.

## Files

- `design.py` — packing, strings, MPPT, checks (no CAD dependency).
- `drawing.py` — sheet drawing; positions follow the example PEA sheets.
- `template.py` — DWG conversion (cached in `.cache/`), title-block filling.
- `plot.py` — PDF/PNG plotting.
