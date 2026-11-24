"""figures.extract on small PDFs written here, one for each kind of layout it once got wrong.
A crop is rendered at 3 pixels per point and keeps 3 points of margin around what it shows, so
sizes are checked in points: what is drawn plus 6.
"""

import pytest
from conftest import DATA

from scholarmind import figures

FONTS = "<< /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >>"
LINE = (
    "The planner keeps a short memory of the scene and replans whenever the measured state "
    "drifts away"
)


def pdf(*pages: str, form: tuple[str, str] | None = None) -> bytes:
    """Letter-size pages with these content streams; `form` is (BBox, content) of /Fm1."""
    n = len(pages)
    xobject = f" /XObject << /Fm1 {3 + 2 * n} 0 R >>" if form else ""
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{kids}] /Count {n} >>"]
    for i, content in enumerate(pages):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font {FONTS}{xobject} >> /Contents {4 + 2 * i} 0 R >>"
        )
        objects.append(_stream("", content))
    if form:
        objects.append(_stream(f"/Type /XObject /Subtype /Form /BBox [{form[0]}]", form[1]))
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out + trailer.encode()


def _stream(head: str, data: str) -> str:
    return f"<< {head} /Length {len(data)} >>\nstream\n{data}\nendstream"


def text(x: float, y: float, s: str, size: float = 10) -> str:
    s = s.replace("(", r"\(").replace(")", r"\)")
    return f"BT /F1 {size} Tf {x} {y} Td ({s}) Tj ET\n"


def para(top: float, lines: int) -> str:
    """Body text: 10 pt lines 12 pt apart, the first on baseline `top`."""
    return "".join(text(72, top - 12 * k, LINE) for k in range(lines))


def rule(y: float, x0: float = 150, x1: float = 460) -> str:
    return f"{x0} {y} {x1 - x0} 0.6 re f\n"


def table(top: float, rows: int, size: float = 9) -> str:
    """A header and `rows` rows of cells between rules from x 150 to 460, the top rule at `top`:
    18.6 + 11 * rows points high."""

    def cells(y, values):
        return "".join(text(150 + 85 * i, y, v, size) for i, v in enumerate(values))

    out = rule(top) + cells(top - 10, ["Method", "Lift", "Press", "Success"]) + rule(top - 14)
    for k in range(rows):
        out += cells(top - 24 - 11 * k, [f"Variant {k + 1}", "92", "48", "32"])
    return out + rule(top - 18 - 11 * rows)


def points(item: dict) -> tuple[float, float]:
    return item["width"] / 3, item["height"] / 3


def test_narrow_lines_on_one_side():
    """Narrow lines all on the left half (a narrow single column) once found no right column."""
    assert figures.extract((DATA / "paper.pdf").read_bytes()) == []


def test_table_header_above_the_text_frame():
    """A table at the top of three pages of ten has its header row above where the body text
    starts, the same words on each: that is not a running head, the one on every page is."""
    pages = []
    for n in range(1, 11):
        head = text(72, 760, "Planner, a technical report", 8) + text(300, 40, str(n), 8)
        if n in (2, 3, 4):
            caption = text(150, 660, f"Table {n - 1}: Success of the planner variants.", 9)
            pages.append(head + table(736, 4) + caption + para(630, 45))
        else:
            pages.append(head + para(712, 52))
    items = figures.extract(pdf(*pages))
    assert [(i["label"], i["page"]) for i in items] == [(f"Table {n}", n + 1) for n in (1, 2, 3)]
    for item in items:  # from the top rule above the header to the bottom rule
        assert points(item) == pytest.approx((316, 18.6 + 44 + 6), abs=1)


def test_clipped_slide():
    """A slide trimmed in LaTeX: pdfium reports the whole slide, reaching up over the table
    above, while only the blue box in the clip shows."""
    slide = "1 1 1 rg 0 0 600 400 re f 0.2 0.4 0.8 rg 150 50 300 120 re f"
    page = (
        para(720, 10)
        + text(150, 588, "Table 1: Accuracy of three methods.", 9)
        + table(580, 3)
        + "q 156 420 300 100 re W n 0.5 0 0 0.5 156 400 cm /Fm1 Do Q\n"
        + text(156, 405, "Fig. 1: A slide trimmed to its diagram.", 9)
        + para(380, 24)
    )
    table_1, figure_1 = figures.extract(pdf(page, form=("0 0 600 400", slide)))
    assert (table_1["label"], figure_1["label"]) == ("Table 1", "Fig. 1")
    assert points(table_1) == pytest.approx((316, 18.6 + 33 + 6), abs=1)
    assert points(figure_1) == pytest.approx((150 + 6, 60 + 6), abs=1)  # the blue box


def test_figure_far_above_its_caption():
    """43 points of white between a figure and its caption."""
    page = (
        para(712, 20)
        + "0.2 0.4 0.8 rg 200 380 200 70 re f 0 g\n"
        + text(72, 330, "Fig. 1: The diagram sits well above its caption.", 9)
        + para(300, 18)
    )
    (item,) = figures.extract(pdf(page))
    assert points(item) == pytest.approx((206, 76), abs=1)


def test_table_rows_in_body_size():
    """Rows in the body font size spanning the column: a wide gap between the first cell and
    the rest, and mostly digits, make them cells and not lines of text."""
    numbers = "0.7932 27.07 0.2547 0.9027 29.47 0.2562 0.8160 23.21 0.2322"
    header = text(72, 562, "Partitioner variant") + text(185, 562, "SSIM PSNR LPIPS " * 3)
    rows = "".join(
        text(72, 546 - 12 * k, f"Ours, setting {k + 1}") + text(175, 546 - 12 * k, numbers)
        for k in range(4)
    )
    page = (
        para(712, 10)
        + text(72, 580, "Table 2: Partitioning ablation.", 9)
        + rule(572, 72, 515)
        + header
        + rule(557, 72, 515)
        + rows
        + rule(505, 72, 515)
        + para(480, 32)
    )
    (item,) = figures.extract(pdf(page))
    assert points(item) == pytest.approx((443 + 6, 67.6 + 6), abs=1)


def test_numbered_heading_under_a_table():
    """A numbered heading in body size, its number a segment of its own, closes the table."""
    page = (
        para(712, 10)
        + text(150, 580, "Table 3: Quality without fine-tuning.", 9)
        + table(572, 3)
        + text(72, 505, "4.4")
        + text(100, 505, "COMPOSABILITY EXPERIMENTS")
        + para(488, 32)
    )
    (item,) = figures.extract(pdf(page))
    assert points(item) == pytest.approx((316, 18.6 + 33 + 6), abs=1)


def test_subcaption_of_three_lines():
    """The lines of a subcaption under a figure hang on each other."""
    sub = "(b) MPC rollout results in simulation tasks, with the models trained"
    page = (
        para(712, 10)
        + "0.2 0.4 0.8 rg 200 500 200 80 re f 0 g\n"
        + "".join(text(150, 491 - 10 * k, sub, 9) for k in range(3))
        + text(150, 450, "Fig. 2: Rollouts in simulation.", 9)
        + para(420, 26)
    )
    (item,) = figures.extract(pdf(page))
    width, height = points(item)
    assert 580 - 471 + 6 <= height <= 580 - 466 + 6  # down to the third line's descenders


def test_tables_with_captions_below():
    """Three tables, each caption under its table: each caption takes the table it is close to,
    not the next one down."""
    page = (
        para(712, 6)
        + table(630, 3)
        + text(150, 568, "Table 1: Ablation of the planner.", 9)
        + table(545, 5)
        + text(150, 461, "Table 2: Ablation of the executor.", 9)
        + table(438, 2)
        + text(150, 387, "Table 3: Ablation of the memory.", 9)
        + para(360, 22)
    )
    items = figures.extract(pdf(page))
    assert [i["label"] for i in items] == ["Table 1", "Table 2", "Table 3"]
    heights = [points(i)[1] for i in items]
    assert heights == pytest.approx([18.6 + 11 * n + 6 for n in (3, 5, 2)], abs=1)
