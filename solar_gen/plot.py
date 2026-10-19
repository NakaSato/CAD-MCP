"""Plot model-space sheets (A3 frames) to a multi-page PDF plus one PNG per sheet."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

import ezdxf  # noqa: E402
from ezdxf.addons.drawing import Frontend, RenderContext  # noqa: E402
from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration, LineweightPolicy  # noqa: E402
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # noqa: E402

FALLBACK_FONT = "Tahoma.ttf"   # used only when no SHX font folder is given; has Thai glyphs


def plot_sheets(dxf_path: Path, origins, pdf_path: Path, png_dir: Path, size=(420.0, 297.0),
                fonts_dir: Path | None = None) -> Path:
    if fonts_dir:   # real SHX fonts (e.g. cordia.shx) - ezdxf renders unifont SHX files directly
        ezdxf.options.support_dirs = [str(fonts_dir)]
    doc = ezdxf.readfile(dxf_path)
    for img in doc.objects.query("IMAGEDEF"):   # resolve images relative to the DXF, like CAD does
        path = Path(img.dxf.filename)
        if not path.is_absolute():
            img.dxf.filename = str((dxf_path.parent / path).resolve())
    if not fonts_dir:
        for st in doc.styles:   # in memory only - the DXF keeps its own fonts
            if str(st.dxf.font).lower().endswith(".shx") or not st.dxf.font:
                st.dxf.font = FALLBACK_FONT
    fig = plt.figure(figsize=(16.535, 11.693))
    ax = fig.add_axes([0, 0, 1, 1])
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, lineweight_policy=LineweightPolicy.RELATIVE)
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(doc.modelspace(), finalize=False)
    ax.set_aspect("equal")
    ax.axis("off")
    with PdfPages(pdf_path) as pdf:
        for i, (x0, y0) in enumerate(origins):
            ax.set_xlim(x0, x0 + size[0])
            ax.set_ylim(y0, y0 + size[1])
            pdf.savefig(fig)
            fig.savefig(png_dir / f"sheet{i:02d}.png", dpi=110)
    plt.close(fig)
    return pdf_path
