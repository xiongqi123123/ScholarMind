"""Figures and tables cropped out of a paper PDF, for the reports' [[图:Fig. 2]] markers.
pypdfium2 finds and renders them, Pillow writes the PNGs. Ported from the prototype checked by
eye on papers of several layouts (Temp/figures/extract.py, 2026-11-23).

How it works, per page:
1. Text is grouped into segments: runs of characters on one baseline without a gap wider than
   the font size (a justified text line stays whole, table cells split apart).
2. Body text is the most common font size; a body line is a line of text of that size spanning
   most of its column (plus the short last line and the first line of a paragraph). A row of
   table cells is not a line of text: it has several wide gaps, or is mostly digits. Columns and
   the text frame (where body lines start and end vertically) come from the body lines of the
   whole document. Outside the frame, text at the same height on many pages is a running head
   or a page number.
3. A caption starts a segment with "Fig./Figure/Table/TABLE <number>" followed by ":" "." "|"
   or a dash, or by a change of font (a bold label, as in "Figure 1 Left to right ..."), or is
   nothing but the label (IEEE journals' "TABLE I"). It must not sit right under a body line
   (that is running text). The caption block grows down line by line while the next line is a
   single run of text aligned with it; a label that turns out to be inside another caption
   ("... as in Table 4. Insets ...") is dropped.
4. The search area is the caption's column, or both columns if the caption crosses the gutter,
   narrowed to one side when text or another caption sits beside the caption (wrapped tables,
   side-by-side figures). Content is searched on both sides of the caption up to the nearest
   body line, numbered heading, other caption, running head or page edge; walking away from the
   caption it stops at an empty gap wider than 5 body font sizes before the first object and
   1.6 after it. Content is graphics (images, vector paths, embedded PDF forms; for images and
   forms what they actually show, since pdfium's bounds ignore clipping) and non-body text. A
   figure keeps the text within 1.8 body font sizes of its graphics, chained (labels, legends,
   subcaptions of several lines). The side with graphics wins; with graphics on both sides the
   one closer to the caption, figures preferring above and tables below when it is a tie.
5. Two captions that claim the same zone (a table below its caption sitting above a figure
   whose caption is below it) split it at the widest vertical gap.
6. The crop is rendered and trimmed to its non-white pixels. Nothing found, or an implausible
   box: the whole page is rendered.

Coordinates are PDF points with the origin at the bottom-left of the page.
"""

import ctypes
import io
import math
import re
import statistics
from dataclasses import dataclass, field

import pypdfium2 as pdfium
import pypdfium2.raw as R
from PIL import ImageChops

LABEL = r"(Figure|Fig\.|FIGURE|FIG\.|Table|TABLE|Tab\.)\s*([A-Z]?\d+|[IVXLC]+)"
CAPTION = re.compile(LABEL + r"\s*([:.|]|—|–)")
LABEL_ONLY = re.compile(LABEL + r"\s*$")
LABEL_WORD = re.compile(LABEL + r"\s+\S")
# "4.4 COMPOSABILITY", "B.2.1 Ablations", "IV. Experiments", "A Proxy and metric parameters"
HEADING = re.compile(r"(\d+(\.\d+)*\.?|[A-Z](\.\d+)+\.?|[A-Z]|[IVX]+\.)\s+[A-Z][A-Za-z]")
GRAPHICS = (R.FPDF_PAGEOBJ_PATH, R.FPDF_PAGEOBJ_IMAGE, R.FPDF_PAGEOBJ_SHADING, R.FPDF_PAGEOBJ_FORM)
SCALE = 3  # 216 dpi
MAX_PX = 2000  # longest side of a crop
PAD = 3  # points of margin around a crop
WHITE = 248  # gray level above which a pixel counts as background when trimming


@dataclass(eq=False)
class Seg:
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    text: str
    ci: list = field(default_factory=list)  # char index per text position, -1 for added spaces
    body: bool = False
    margin: bool = False  # running head, page number
    big: float = 0  # largest char: small caps lines start lower case letters small

    @property
    def w(self):
        return self.x1 - self.x0


@dataclass(eq=False)
class Item:
    kind: str  # figure | table
    label: str  # as written, "Fig. 2", "TABLE I"
    number: str
    page: int  # 0-based here, 1-based in the manifest
    cap: Seg  # caption block
    lines: list  # the caption's segments
    span: tuple  # x0, x1 searched
    caption: str = ""
    side: str = ""  # above | below: where the content is relative to the caption
    box: tuple | None = None  # x0, y0, x1, y1 in PDF coordinates (y up)


def segments(textpage) -> list[Seg]:
    segs, cur, matrix = [], None, R.FS_MATRIX()

    def close():
        while cur.text and cur.text[-1] == " ":
            cur.text, cur.ci = cur.text[:-1], cur.ci[:-1]
        segs.append(cur)

    for i in range(textpage.count_chars()):
        if R.FPDFText_IsHyphen(textpage.raw, i) == 1:  # a hyphen at a line end
            if cur:
                cur.text, cur.ci = cur.text + "\x02", cur.ci + [-1]
            continue
        ch = textpage.get_text_range(i, 1)
        if ch in "\r\n" or not ch:
            continue
        if R.FPDFText_IsGenerated(textpage.raw, i) == 1 or ch.isspace():
            if cur and not cur.text.endswith(" "):
                cur.text, cur.ci = cur.text + " ", cur.ci + [-1]
            continue
        x0, y0, x1, y1 = textpage.get_charbox(i)
        # effective size: a table shrunk with \resizebox keeps its nominal font size
        R.FPDFText_GetMatrix(textpage.raw, i, matrix)
        size = R.FPDFText_GetFontSize(textpage.raw, i) * math.hypot(matrix.c, matrix.d)
        if x1 <= x0 and y1 <= y0:
            continue
        if cur:
            same_line = abs((y0 + y1) / 2 - (cur.y0 + cur.y1) / 2) < 0.5 * max(size, cur.size)
            if same_line and -size < x0 - cur.x1 < max(size, 4):
                cur.x1, cur.y0, cur.y1 = max(cur.x1, x1), min(cur.y0, y0), max(cur.y1, y1)
                cur.text, cur.ci, cur.big = cur.text + ch, cur.ci + [i], max(cur.big, size)
                continue
            close()
        cur = Seg(x0, y0, x1, y1, size, ch, [i], big=size)
    if cur:
        close()
    return segs


def font(textpage, i) -> str:
    buf = ctypes.create_string_buffer(128)
    R.FPDFText_GetFontInfo(textpage.raw, i, buf, 128, None)
    return buf.value.decode(errors="replace").split("+")[-1]


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def pct(xs, q):
    return xs[min(len(xs) - 1, int(q * len(xs)))]


class Layout:
    """Body font size, columns and text frame of the whole document."""

    def __init__(self, pages: list[list[Seg]], width: float):
        sizes = [round(s.size * 2) / 2 for p in pages for s in p for _ in range(len(s.text))]
        self.body = statistics.mode(sizes) if sizes else 10.0
        lines = [s for p in pages for s in p if self.body_size(s) and s.w > 0.25 * width]
        narrow = [s for s in lines if s.w < 0.55 * width]
        left = [s for s in narrow if (s.x0 + s.x1) / 2 < width / 2]
        right = [s for s in narrow if (s.x0 + s.x1) / 2 >= width / 2]
        if left and right and len(narrow) > 0.6 * len(lines):  # short lines on one side: 1 column
            self.cols = [self._edges(left), self._edges(right)]
        else:
            self.cols = [self._edges(lines or [Seg(0, 0, width, 0, 10, "")])]
        self.frame_x = (self.cols[0][0], self.cols[-1][1])
        for p in pages:
            self.mark_body(p)
        body = [s for p in pages for s in p if s.body]
        ys0, ys1 = sorted(s.y0 for s in body), sorted(s.y1 for s in body)
        self.frame_y = (pct(ys0, 0.01), pct(ys1, 0.99)) if body else (0, 1e9)

        # outside the text frame, text at the same height on a good share of the pages is a
        # running head or a page number (digits count as equal); floats may stick out of the
        # frame, so these are what bounds them. A table header sticking out of the frame repeats
        # its words ("Model", "Task") on a few pages only.
        def outside(s):
            return s.y0 > self.frame_y[1] + 2 or s.y1 < self.frame_y[0] - 2

        def key(s):
            return round(s.y0 / 3), re.sub(r"\d+", "#", s.text).strip()

        counts = {}
        for p in pages:
            for k in {key(s) for s in p if outside(s)}:
                counts[k] = counts.get(k, 0) + 1
        often = max(3, 0.35 * len(pages))
        for p in pages:
            for s in p:
                if outside(s) and counts[key(s)] >= often:
                    s.margin = True

    @staticmethod
    def _edges(segs):
        return pct(sorted(s.x0 for s in segs), 0.05), pct(sorted(s.x1 for s in segs), 0.95)

    def body_size(self, s):
        return abs(s.size - self.body) < 0.6

    def col(self, x0, x1):
        """The column a horizontal range belongs to, or the whole frame if it crosses columns."""
        for c in self.cols:
            if x0 >= c[0] - 15 and x1 <= c[1] + 15:
                return c
        return self.frame_x

    def mark_body(self, segs):
        rows = {}
        for s in segs:
            c = self.col(s.x0, s.x1)
            # the whole line in this column: inline math can split a text line into segments
            row = sorted(
                (
                    t
                    for t in segs
                    if t is s
                    or abs((t.y0 + t.y1 - s.y0 - s.y1) / 2) < 0.3 * s.size
                    and t.x0 >= c[0] - 15
                    and t.x1 <= c[1] + 15
                ),
                key=lambda t: t.x0,
            )
            x0, x1 = row[0].x0, max(t.x1 for t in row)
            gaps = [b.x0 - a.x1 for a, b in zip(row, row[1:], strict=False)]
            text = "".join(t.text for t in row)
            filled = sum(t.w for t in row) >= 0.8 * (x1 - x0)
            # not table cells: inline math leaves a gap or two, cells many, and wider ones
            loose = filled and max(gaps, default=0) < 2 * s.size
            text_line = (
                loose
                and sum(g >= s.size for g in gaps) <= 2
                and sum(ch.isdigit() for ch in text) < 0.4 * len(text)
            )
            wide = x1 - x0 >= 0.7 * (c[1] - c[0])
            rows[id(s)] = (c, row, loose, filled and wide)
            s.body = self.body_size(s) and text_line and wide
        # next to a body line the tests are looser: table rows are not that close to a paragraph
        for s in segs:  # last line of a paragraph: from the column edge right under a body line
            c, row, loose, _ = rows[id(s)]
            if s.body or not self.body_size(s) or not loose or abs(row[0].x0 - c[0]) > 3:
                continue
            if any(t.body and 0 < t.y0 - s.y1 < s.size and abs(t.x0 - c[0]) < 20 for t in segs):
                for t in row:
                    t.body = True
        for s in segs:  # first line of a paragraph: a wide line right above a body line
            c, row, _, wide_filled = rows[id(s)]
            if s.body or not self.body_size(s) or not wide_filled:
                continue
            if any(
                t.body and 0 < s.y0 - t.y1 < 0.8 * s.size and overlap(t.x0, t.x1, s.x0, s.x1) > 0
                for t in segs
            ):
                for t in row:
                    t.body = True


def is_caption(s, textpage):
    """(word, number) if the segment starts a caption."""
    m = CAPTION.match(s.text) or LABEL_ONLY.match(s.text)
    if not m:
        m = LABEL_WORD.match(s.text)
        if not m:
            return None
        after = next((c for c in s.ci[m.end() - 1 :] if c >= 0), -1)
        if after < 0 or font(textpage, s.ci[0]) == font(textpage, after):
            return None  # "Figure 2 presents ..." in running text
    return m.group(1), m.group(2)


def find_captions(segs, layout, pageno, textpage) -> list[Item]:
    items = []
    for s in sorted(segs, key=lambda s: -s.y1):
        found = is_caption(s, textpage)
        if not found:
            continue
        word, number = found
        kind = "table" if word.lower().startswith("tab") else "figure"
        it = Item(kind, f"{word} {number}", number, pageno, s, [s], layout.col(s.x0, s.x1))
        # a body line right above at about paragraph leading: maybe running text
        it.after_body = any(
            t.body and 0 <= t.y0 - s.y1 < 0.6 * s.size and overlap(t.x0, t.x1, s.x0, s.x1) > 0
            for t in segs
        )
        if it.after_body and not (CAPTION.match(s.text) or LABEL_ONLY.match(s.text)):
            continue  # "Figure 2 presents ..." at a line start in a paragraph
        items.append(it)
    starts = {id(it.cap) for it in items}
    for it in items:  # grow the caption block downwards, stopping at another caption
        first, block = it.cap, it.cap
        while True:
            row = [
                t
                for t in segs
                if t not in it.lines
                and -0.3 * first.size < block.y0 - t.y1 < 0.6 * first.size
                and overlap(t.x0, t.x1, first.x0, first.x1) > 0
            ]
            if not row or any(abs(t.big - first.big) > 1 or id(t) in starts for t in row):
                break
            x0, x1 = min(t.x0 for t in row), max(t.x1 for t in row)
            if sum(t.w for t in row) < 0.85 * (x1 - x0):  # cells, not a line of text
                break
            if (
                abs(x0 - first.x0) > 1.5 * first.size
                and abs((x0 + x1 - first.x0 - first.x1) / 2) > first.size
            ):
                break  # neither aligned with the first line nor centred under it
            it.lines += row
            block = Seg(
                min(block.x0, x0),
                min(t.y0 for t in row),
                max(block.x1, x1),
                block.y1,
                first.size,
                "",
            )
        it.cap = block
    for it in items:  # right under another caption's last line: maybe its continuation
        s = it.lines[0]
        it.prev = next(
            (
                o
                for o in items
                if o is not it
                and -0.5 * s.size < o.cap.y0 - s.y1 < 0.6 * s.size
                and overlap(o.cap.x0, o.cap.x1, s.x0, s.x1) > 0
            ),
            None,
        )
    return items


def dedupe(items):
    """A label found more than once in the document: the candidates right under a body line or
    under another caption ("... as in" / "Table 4. Insets ...") give way to one that is not;
    those under a caption are merged back into it. Several clean ones are all kept."""
    groups = {}
    for it in items:
        groups.setdefault((it.kind, it.number), []).append(it)
    drop = set()
    for group in groups.values():
        clean = [it for it in group if not it.after_body and not it.prev]
        if not clean:
            continue
        for it in group:
            if it in clean:
                continue
            drop.add(id(it))
            if it.prev:
                p = it.prev
                p.lines += it.lines
                p.cap = Seg(
                    min(p.cap.x0, it.cap.x0),
                    min(p.cap.y0, it.cap.y0),
                    max(p.cap.x1, it.cap.x1),
                    p.cap.y1,
                    p.cap.size,
                    "",
                )
    kept = [it for it in items if id(it) not in drop]
    for it in kept:
        it.lines.sort(key=lambda t: (-round(t.y1), t.x0))
        # a word split over two lines is joined, as report.extract_pages does
        it.caption = re.sub("\x02 ?", "", " ".join(t.text for t in it.lines))
    return kept


def narrow(items, segs, layout):
    """Text or another caption beside the caption: keep the search to the caption's side."""
    for it in items:
        c, (sx0, sx1) = it.cap, it.span
        for t in segs:
            if (
                t in it.lines
                or not overlap(t.y0, t.y1, c.y0, c.y1)
                or overlap(t.x0, t.x1, sx0, sx1) <= 0
            ):
                continue
            text = t.body or layout.body_size(t) or any(t in o.lines for o in items)
            if t.x1 < c.x0 - layout.body and text:
                sx0 = max(sx0, t.x1 + 2)
            elif t.x0 > c.x1 + layout.body and text:
                sx1 = min(sx1, t.x0 - 2)
        it.span = (sx0, sx1)


def visible(page, obj, box, height):
    """The part of an image or embedded PDF that actually shows, or None. The page region inside
    its bounds is rendered with and without the object, and the difference is what it draws. The
    bounds pdfium reports ignore clipping, so a slide exported from PowerPoint and trimmed in
    LaTeX reports the whole slide, white background included. This moves the object: measure on
    a copy of the document that is not rendered afterwards.
    """
    x0, y0, x1, y1 = box
    w = page.get_size()[0]
    crop = (max(0, x0), max(0, y0), max(0, w - x1), max(0, height - y1))
    with_obj = page.render(scale=1, crop=crop).to_pil()
    obj.transform(pdfium.PdfMatrix().translate(0, 3000))  # off the page, then back
    without = page.render(scale=1, crop=crop).to_pil()
    obj.transform(pdfium.PdfMatrix().translate(0, -3000))
    # light gray drawn on white still counts: a threshold of 16 cut off a pale grid
    diff = ImageChops.difference(with_obj, without).convert("L")
    bb = diff.point(lambda v: 255 if v > 2 else 0).getbbox()
    if not bb:
        return None
    left, top = crop[0], height - crop[3]
    return left + bb[0], top - bb[3], left + bb[2], top - bb[1]


def graphics(page, width, height):
    out = []
    for o in page.get_objects(max_depth=1):
        if o.type not in GRAPHICS:
            continue
        box = o.get_bounds()
        if o.type in (R.FPDF_PAGEOBJ_FORM, R.FPDF_PAGEOBJ_IMAGE):
            box = visible(page, o, box, height)
            if not box:
                continue
        x0, y0, x1, y1 = box
        if x1 - x0 <= 0 and y1 - y0 <= 0:
            continue
        if (x1 - x0) * (y1 - y0) > 0.8 * width * height:  # page background
            continue
        out.append((x0, y0, x1, y1))
    return out


def claim(it, side, items, segs, objs, layout, height):
    """(content boxes, any graphics among them) between the caption and the nearest stop."""
    sx0, sx1 = it.span
    up = side == "above"
    edge = it.cap.y1 if up else it.cap.y0
    limit = height if up else 0

    def inspan(x0, x1):
        return overlap(x0, x1, sx0 - 2, sx1 + 2) > 0.5 * max(x1 - x0, 0.1)

    def touches(x0, x1):  # a stop needs only to reach in
        return overlap(x0, x1, sx0, sx1) > 5

    stops = [s for s in segs if (s.body or s.margin) and touches(s.x0, s.x1) and s not in it.lines]

    def heading(s):
        """Alone on its line and numbered ("4.4 COMPOSABILITY": the number is a segment of its
        own, a small caps heading has body size) or, below a table, larger than body text
        (table text never is)."""
        if s.size < layout.body - 0.6:
            return False
        row = sorted(
            (
                t
                for t in segs
                if abs((t.y0 + t.y1 - s.y0 - s.y1) / 2) < 0.3 * s.size and touches(t.x0, t.x1)
            ),
            key=lambda t: t.x0,
        )
        if len(row) <= 2 and HEADING.match(" ".join(t.text for t in row)):
            return True
        return it.kind == "table" and len(row) == 1 and s.size > 1.03 * layout.body

    stops += [s for s in segs if touches(s.x0, s.x1) and heading(s)]
    stops += [o.cap for o in items if o is not it and touches(o.cap.x0, o.cap.x1)]
    for s in stops:
        if up and s.y0 >= edge - 1:
            limit = min(limit, s.y0)
        if not up and s.y1 <= edge + 1:
            limit = max(limit, s.y1)
    lo, hi = (edge, limit) if up else (limit, edge)
    in_caption = {id(t) for o in items for t in o.lines}

    # a graphic counts if mostly inside the span or covering most of it (a form's bounds can be
    # wider than the page); it is clipped to the search area below
    def wide(x0, x1):
        return overlap(x0, x1, sx0, sx1) > 0.5 * min(x1 - x0, sx1 - sx0)

    boxes = [(b, True) for b in objs if inspan(b[0], b[2]) or wide(b[0], b[2])]
    boxes += [
        ((s.x0, s.y0, s.x1, s.y1), False)
        for s in segs
        if not s.body and not s.margin and id(s) not in in_caption and inspan(s.x0, s.x1)
    ]
    cand = []
    for (x0, y0, x1, y1), is_graphic in boxes:  # clip to the band
        y0, y1 = max(y0, lo), min(y1, hi)
        if y1 - y0 > 0.5 or (y1 >= y0 and is_graphic):  # rules are flat
            cand.append(((max(x0, sx0 - 10), y0, min(x1, sx1 + 10), y1), is_graphic))
    cand.sort(key=lambda c: c[0][1] if up else -c[0][3])
    got, reach = [], edge
    for b, is_graphic in cand:  # walk away from the caption, stop at a wide empty gap
        gap = (b[1] - reach) if up else (reach - b[3])
        if gap > (1.6 if got else 5) * layout.body:
            break
        got.append((b, is_graphic))
        reach = max(reach, b[3]) if up else min(reach, b[1])
    solid = [b for b, g in got if g and b[3] - b[1] >= 1.5 * layout.body]
    if it.kind == "figure" and solid:
        # a figure keeps what lies within 1.8 body font sizes of its real graphics, chained:
        # labels, legends and subcaptions of several lines sit that close, a title block above a
        # teaser figure (bridged by a link underline) does not. A rule or an underline does not
        # reach further.
        _, u0, _, u1 = union(solid)
        near, rest, grew = [], list(got), True
        while grew:
            grew = False
            for bg in list(rest):
                b = bg[0]
                if b[3] >= u0 - 1.8 * layout.body and b[1] <= u1 + 1.8 * layout.body:
                    near.append(bg)
                    rest.remove(bg)
                    if b[3] - b[1] >= 2:
                        u0, u1 = min(u0, b[1]), max(u1, b[3])
                    grew = True
        got = near
    return [b for b, _ in got], any(g for _, g in got)


def gap_to(it, side, boxes):
    """Empty space between the caption and the nearest content box on that side."""
    if side == "above":
        return min(b[1] for b in boxes) - it.cap.y1
    return it.cap.y0 - max(b[3] for b in boxes)


def solid(found, layout):
    """Graphics, and more than a sliver (a fraction bar or a footnote rule is not a figure)."""
    boxes, graphic = found
    if not boxes or not graphic:
        return False
    x0, y0, x1, y1 = union(boxes)
    return y1 - y0 >= 1.5 * layout.body and x1 - x0 >= 30


def union(boxes):
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def locate(items, segs, objs, layout, height):
    for it in items:
        sides = ("above", "below") if it.kind == "figure" else ("below", "above")
        found = {s: claim(it, s, items, segs, objs, layout, height) for s in sides}
        with_graphics = [s for s in sides if solid(found[s], layout)]
        non_empty = [s for s in sides if found[s][0]]
        if len(with_graphics) == 2:
            # both sides: the caption's own content is the closer one (only the caption skip
            # between them, another float's content is a float separation away). A caption
            # below its table, or above its figure, then still finds its content.
            gap = {s: gap_to(it, s, found[s][0]) for s in sides}
            if gap[sides[1]] < gap[sides[0]] - 0.5 * layout.body:
                with_graphics = [sides[1]]
        it.side = (with_graphics or non_empty or sides)[0]
        it._found, it._boxes = found, found[it.side][0]

    def clash(a, b):  # a's content (below its caption) runs into b's (above its caption)
        if a.side != "below" or b.side != "above" or not a._boxes or not b._boxes:
            return False
        if not overlap(*a.span, *b.span) or not a.cap.y0 > b.cap.y1:
            return False
        ua, ub = union(a._boxes), union(b._boxes)
        return overlap(ua[1], ua[3], ub[1], ub[3]) > 0

    def free(it, side):  # solid content on that side that no other item has taken
        boxes = it._found[side][0]
        if not solid(it._found[side], layout):
            return False
        u = union(boxes)
        return not any(
            o is not it
            and o._boxes
            and overlap(*it.span, *o.span)
            and overlap(u[1], u[3], union(o._boxes)[1], union(o._boxes)[3]) > 0
            for o in items
        )

    # two captions claiming the same zone: one of them turns to its other side if that side has
    # content nobody else took, otherwise the zone is split at the widest gap
    for a in items:
        for b in items:
            if a is b or not clash(a, b):
                continue
            for it, other in ((a, "above"), (b, "below")):
                if free(it, other):
                    it.side, it._boxes = other, it._found[other][0]
                    break
            else:
                split(a, b)
    for it in items:
        if not it._boxes:
            continue
        x0, y0, x1, y1 = union(it._boxes)
        if it.side == "above":  # keep the padded crop clear of the caption
            y0 = max(y0, it.cap.y1 + PAD)
        else:
            y1 = min(y1, it.cap.y0 - PAD)
        if y1 - y0 >= 1.5 * layout.body and x1 - x0 >= 30:  # else the whole page is shown
            it.box = (x0, y0, x1, y1)


def split(a, b):
    """a (content below its caption) and b (content above its caption) share a zone: cut it at
    the widest vertical gap."""
    zone = sorted(set(a._boxes) | set(b._boxes), key=lambda x: -x[3])
    best, cut, low = -1, None, zone[0][1]
    for prev, x in zip(zone, zone[1:], strict=False):
        low = min(low, prev[1])
        if low - x[3] > best:
            best, cut = low - x[3], (low + x[3]) / 2
    if cut is not None:
        a._boxes = [x for x in a._boxes if (x[1] + x[3]) / 2 > cut]
        b._boxes = [x for x in b._boxes if (x[1] + x[3]) / 2 < cut]


def render(page, box) -> dict:
    """{"image": PNG, "width", "height"} of the box trimmed to its non-white pixels, or of the
    whole page without a box."""
    w, h = page.get_size()
    if box is None:
        img = page.render(scale=1.5).to_pil()
    else:
        x0, y0, x1, y1 = box
        x0, y0, x1, y1 = max(0, x0 - PAD), max(0, y0 - PAD), min(w, x1 + PAD), min(h, y1 + PAD)
        scale = min(SCALE, MAX_PX / max(x1 - x0, y1 - y0))
        img = page.render(scale=scale, crop=(x0, y0, w - x1, h - y1)).to_pil()
        ink = img.convert("L").point(lambda v: 255 if v < WHITE else 0).getbbox()
        if ink:
            m = round(PAD * scale)
            img = img.crop(
                (
                    max(0, ink[0] - m),
                    max(0, ink[1] - m),
                    min(img.width, ink[2] + m),
                    min(img.height, ink[3] + m),
                )
            )
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return {"image": out.getvalue(), "width": img.width, "height": img.height}


def extract(pdf: bytes) -> list[dict]:
    """Every figure and table of the paper, in reading order: {"kind": "figure" or "table",
    "label": as the caption writes it ("Fig. 2", "TABLE I"), "caption", "page" (1-based),
    "image": PNG bytes, "width" and "height" in pixels}. An item whose content was not found
    shows its whole page.
    """
    doc = pdfium.PdfDocument(pdf)
    try:
        pages = [doc[i] for i in range(len(doc))]
        textpages = [p.get_textpage() for p in pages]
        segs = [segments(t) for t in textpages]
        layout = Layout(segs, pages[0].get_size()[0])
        found = (find_captions(segs[i], layout, i, textpages[i]) for i in range(len(pages)))
        items = dedupe([it for page_items in found for it in page_items])
        # visible() moves objects off the page and back: measure on a second copy of the
        # document, so the pages rendered below are untouched
        probe = pdfium.PdfDocument(pdf)
        try:
            objs = [graphics(probe[i], *probe[i].get_size()) for i in range(len(probe))]
        finally:
            probe.close()
        # outside the text frame and at the same place on a good share of the pages (as for
        # running heads): a header rule, a logo; not a table's top rule on a few pages
        fy0, fy1 = layout.frame_y

        def key(b):
            return tuple(round(v) for v in b)

        def outside(b):
            return b[1] > fy1 + 1 or b[3] < fy0 - 1

        counts = {}
        for page_objs in objs:
            for k in {key(b) for b in page_objs if outside(b)}:
                counts[k] = counts.get(k, 0) + 1
        often = max(3, 0.35 * len(pages))
        for i, page in enumerate(pages):
            on_page = [it for it in items if it.page == i]
            narrow(on_page, segs[i], layout)
            page_objs = [b for b in objs[i] if not (outside(b) and counts[key(b)] >= often)]
            locate(on_page, segs[i], page_objs, layout, page.get_size()[1])
        return [
            {
                "kind": it.kind,
                "label": it.label,
                "caption": it.caption,
                "page": it.page + 1,
            }
            | render(pages[it.page], it.box)
            for it in items
        ]
    finally:
        doc.close()
