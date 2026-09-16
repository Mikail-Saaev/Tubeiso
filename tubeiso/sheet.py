"""Surface de dessin en millimetres papier, deux rendus : SVG et PDF.

La mise en plan est ecrite UNE fois, contre cette interface. Elle sort ensuite
en SVG pour l'apercu dans l'application, et en PDF pour le sous-traitant, sans
qu'aucune cote ne puisse diverger entre les deux — c'est tout l'interet.

Repere : origine en haut a gauche, X vers la droite, Y vers le bas, unites en
millimetres reels sur la feuille. C'est la convention du SVG ; l'adaptateur PDF
retourne l'axe Y, puisque le PDF compte depuis le bas.

**Le PDF est ecrit ici, sans aucune bibliotheque.** La premiere version
s'appuyait sur reportlab, et l'export tombait en panne sur tout poste ou ce
paquet n'etait pas installe — c'est-a-dire sur l'executable distribue. Un plan
qui ne sort pas est un plan qui n'existe pas : la dependance a donc ete
supprimee. Le format PDF utilise ici est volontairement minimal — un catalogue,
des pages, un flux de contenu compresse, les trois polices standard — et ne
depend de rien d'autre que la bibliotheque standard.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

MM_PER_PT = 25.4 / 72.0
PT_PER_MM = 72.0 / 25.4

# Palette du plan. Sobre par necessite : un plan part souvent en photocopie
# noir et blanc, donc aucune information ne repose sur la seule couleur.
INK = "#111111"
GREY = "#6B7280"
LIGHT = "#B9C2CB"
BODY = "#D8DEE4"
RED = "#B3261E"
ORANGE = "#95610B"
GREEN = "#1F6B43"
BLUE = "#0F5F7A"

A4_LANDSCAPE = (297.0, 210.0)
A4_PORTRAIT = (210.0, 297.0)

# Largeurs de trait normalisees, esprit ISO 128.
W_FRAME = 0.70
W_OUTLINE = 0.50
W_THIN = 0.25
W_HAIR = 0.15


@dataclass
class Sheet:
    """Interface commune. Les deux adaptateurs ci-dessous l'implementent."""

    width: float = A4_LANDSCAPE[0]
    height: float = A4_LANDSCAPE[1]

    # --- primitives, redefinies par les adaptateurs
    def line(self, x1, y1, x2, y2, w=W_THIN, colour=INK, dash=None): ...
    def polyline(self, pts, w=W_THIN, colour=INK, dash=None, cap="round",
                 join="round", close=False, fill=None): ...
    def rect(self, x0, y0, x1, y1, w=W_THIN, colour=INK, fill=None): ...
    def circle(self, x, y, r, w=W_THIN, colour=INK, fill=None): ...
    def text(self, x, y, s, size=2.5, anchor="start", bold=False, colour=INK,
             mono=False): ...
    def page_break(self): ...
    def width_of(self, s: str, size: float, bold=False, mono=False) -> float: ...

    # --- helpers communs, ecrits une seule fois
    def hline(self, x0, x1, y, **kw):
        self.line(x0, y, x1, y, **kw)

    def vline(self, x, y0, y1, **kw):
        self.line(x, y0, x, y1, **kw)

    def label_value(self, x0, x1, y, label, value, size=2.4, bold_value=True,
                    colour=INK):
        """Une ligne de cartouche : libelle a gauche, valeur calee a droite."""
        self.text(x0, y, label, size, colour=GREY)
        self.text(x1, y, value, size, anchor="end", bold=bold_value, colour=colour)

    def table(self, x0, y, widths, rows, size=2.3, header=True,
              row_h=4.0, aligns=None, colour=INK):
        """Tableau simple. `rows[0]` est l'en-tete si `header`.

        Retourne l'ordonnee de la derniere ligne ecrite, pour enchainer.
        """
        aligns = aligns or ["start"] * len(widths)
        xs, acc = [], x0
        for w in widths:
            xs.append(acc)
            acc += w
        total = acc - x0
        for r, row in enumerate(rows):
            bold = header and r == 0
            for i, cell in enumerate(row):
                if i >= len(xs):
                    break
                a = aligns[i]
                cx = xs[i] if a == "start" else (
                    xs[i] + widths[i] - 1.2 if a == "end" else xs[i] + widths[i] / 2)
                self.text(cx, y, str(cell), size, anchor=a, bold=bold,
                          colour=GREY if bold else colour)
            if bold:
                self.hline(x0, x0 + total, y + 1.3, w=W_THIN)
                y += row_h + 0.8
            else:
                y += row_h
        return y


_WIDTHS_RAW = {
    "Helvetica":
        "0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,278,278,355,556,556,889,667,191,333,333,389,584,278,333,278,278,556,556,556,556,556,556,556,556,556,556,278,278,584,584,584,556,1015,667,667,722,722,667,611,778,722,278,500,667,556,833,722,778,667,778,722,667,611,722,667,944,667,667,611,278,278,278,469,556,333,556,556,500,556,556,278,556,556,222,222,500,222,833,556,556,556,556,333,500,278,556,500,722,500,500,500,334,260,334,584,350,556,350,222,556,333,1000,556,556,333,1000,667,333,1000,350,611,350,350,222,222,333,333,350,556,1000,333,1000,500,333,944,350,500,667,278,333,556,556,556,556,260,556,333,737,370,556,584,333,737,333,400,584,333,333,333,556,537,278,333,333,365,556,834,834,834,611,667,667,667,667,667,667,1000,722,667,667,667,667,278,278,278,278,722,722,778,778,778,778,778,584,778,722,722,722,722,667,667,611,556,556,556,556,556,556,889,500,556,556,556,556,278,278,278,278,556,556,556,556,556,556,556,584,611,556,556,556,556,500,556,500",
    "Helvetica-Bold":
        "0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,278,333,474,556,556,889,722,238,333,333,389,584,278,333,278,278,556,556,556,556,556,556,556,556,556,556,333,333,584,584,584,611,975,722,722,722,722,667,611,778,722,278,556,722,611,833,722,778,667,778,722,667,611,722,667,944,667,667,611,333,278,333,584,556,333,556,611,556,611,556,333,611,611,278,278,556,278,889,611,611,611,611,389,556,333,611,556,778,556,556,500,389,280,389,584,350,556,350,278,556,500,1000,556,556,333,1000,667,333,1000,350,611,350,350,278,278,500,500,350,556,1000,333,1000,556,333,944,350,500,667,278,333,556,556,556,556,280,556,333,737,370,556,584,333,737,333,400,584,333,333,333,611,556,278,333,333,365,556,834,834,834,611,722,722,722,722,722,722,1000,722,667,667,667,667,278,278,278,278,722,722,778,778,778,778,778,584,778,722,722,722,722,667,667,611,556,556,556,556,556,556,889,556,556,556,556,556,278,278,278,278,611,611,611,611,611,611,611,584,611,611,611,611,611,556,611,556",
    "Courier":
        "0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600,600",
}
_WIDTHS = {name: [int(v) for v in raw.split(",")]
           for name, raw in _WIDTHS_RAW.items()}
_WIDTHS["Courier-Bold"] = _WIDTHS["Courier"]

_FONT_IDS = {"Helvetica": "F1", "Helvetica-Bold": "F2", "Courier": "F3",
             "Courier-Bold": "F3"}


def _pdf_escape(text: str) -> bytes:
    """Chaine PDF litterale, encodee en WinAnsi comme l'annonce la ressource."""
    raw = str(text).encode("cp1252", "replace")
    out = bytearray()
    for b in raw:
        if b in (0x28, 0x29, 0x5C):          # ( ) \
            out += b"\\" + bytes([b])
        elif b < 32 or b > 126:
            out += f"\\{b:03o}".encode("ascii")
        else:
            out.append(b)
    return bytes(out)


def _pdf_meta(text: str) -> bytes:
    """Chaine de metadonnees, en UTF-16BE : le dictionnaire Info n'est pas en
    WinAnsi, et un tiret cadratin y ressortait en « Š »."""
    raw = ("\ufeff" + str(text)).encode("utf-16-be")
    out = bytearray()
    for b in raw:
        if b in (0x28, 0x29, 0x5C):
            out += b"\\" + bytes([b])
        else:
            out.append(b)
    return bytes(out)


def text_width(text: str, font: str, size_pt: float) -> float:
    """Largeur d'un texte, en points."""
    table = _WIDTHS.get(font) or _WIDTHS["Helvetica"]
    total = 0
    for b in str(text).encode("cp1252", "replace"):
        total += table[b] if b < len(table) else 500
    return total * size_pt / 1000.0


class SvgSheet(Sheet):
    """Rendu SVG. Une seule page : l'apercu de l'application ne pagine pas."""

    def __init__(self, width=A4_LANDSCAPE[0], height=A4_LANDSCAPE[1]):
        super().__init__(width, height)
        self.pages: list[list[str]] = [[]]

    # -- outils internes
    @property
    def _cur(self):
        return self.pages[-1]

    @staticmethod
    def _stroke(w, colour, dash):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        return f'stroke="{colour}" stroke-width="{w}"{d}'

    def line(self, x1, y1, x2, y2, w=W_THIN, colour=INK, dash=None):
        self._cur.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'{self._stroke(w, colour, dash)}/>')

    def polyline(self, pts, w=W_THIN, colour=INK, dash=None, cap="round",
                 join="round", close=False, fill=None):
        if len(pts) < 2:
            return
        d = "M " + " L ".join(f"{p[0]:.2f} {p[1]:.2f}" for p in pts)
        if close:
            d += " Z"
        self._cur.append(
            f'<path d="{d}" fill="{fill or "none"}" {self._stroke(w, colour, dash)} '
            f'stroke-linecap="{cap}" stroke-linejoin="{join}"/>')

    def rect(self, x0, y0, x1, y1, w=W_THIN, colour=INK, fill=None):
        self._cur.append(
            f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{x1 - x0:.2f}" '
            f'height="{y1 - y0:.2f}" fill="{fill or "none"}" '
            f'{self._stroke(w, colour, None)}/>')

    def circle(self, x, y, r, w=W_THIN, colour=INK, fill=None):
        self._cur.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="{fill or "none"}" '
            f'{self._stroke(w, colour, None)}/>')

    def text(self, x, y, s, size=2.5, anchor="start", bold=False, colour=INK,
             mono=False):
        family = ("ui-monospace,Menlo,Consolas,monospace" if mono
                  else "Helvetica,Arial,sans-serif")
        self._cur.append(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" fill="{colour}" '
            f'font-family="{family}" font-weight="{"bold" if bold else "normal"}" '
            f'text-anchor="{anchor}">{escape(str(s))}</text>')

    def page_break(self):
        self.pages.append([])

    def width_of(self, s, size, bold=False, mono=False):
        # Les memes metriques que le PDF : les deux sorties doivent tronquer et
        # retourner a la ligne au meme endroit, sinon l'apercu ment.
        font = "Courier" if mono else ("Helvetica-Bold" if bold else "Helvetica")
        return text_width(s, font, size * PT_PER_MM) * MM_PER_PT

    def to_svg(self, page: int = 0) -> str:
        body = "".join(self.pages[page])
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}mm" '
                f'height="{self.height}mm" viewBox="0 0 {self.width} {self.height}">'
                f'<rect width="{self.width}" height="{self.height}" fill="#ffffff"/>'
                f'{body}</svg>')


# ----------------------------------------------------------------------- PDF
#
# Largeurs des glyphes des polices standard PDF, en millemes de point, indexees
# par octet WinAnsi. Ce sont les metriques Adobe officielles ; elles sont dans
# tout lecteur PDF, donc aucune police n'est a incorporer. Sans elles, un texte
# cale a droite ou centre tomberait a cote.
class PdfSheet(Sheet):
    """Rendu PDF vectoriel, ecrit sans dependance externe. Multi-pages."""

    KAPPA = 0.5522847498307936          # approximation d'un quart de cercle

    def __init__(self, path, width=A4_LANDSCAPE[0], height=A4_LANDSCAPE[1],
                 title="", author="tubeiso", subject="", compress=True):
        super().__init__(width, height)
        self.path = path
        self.title, self.author, self.subject = title or "Plan de tube", author, subject
        self.compress = compress
        self._pages: list[bytearray] = []
        self._cur = bytearray()
        self._state: dict = {}

    # -- conversions
    def _x(self, v):
        return float(v) * PT_PER_MM

    def _y(self, v):
        return (self.height - float(v)) * PT_PER_MM

    @staticmethod
    def _rgb(colour: str) -> tuple:
        c = str(colour).lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        try:
            return (int(c[0:2], 16) / 255.0, int(c[2:4], 16) / 255.0,
                    int(c[4:6], 16) / 255.0)
        except (ValueError, IndexError):                          # pragma: no cover
            return (0.0, 0.0, 0.0)

    def _w(self, *parts) -> None:
        self._cur += (" ".join(parts) + "\n").encode("ascii")

    def _stroke_state(self, w, colour, dash=None, cap=None, join=None):
        r, g, b = self._rgb(colour)
        self._w(f"{r:.3f}", f"{g:.3f}", f"{b:.3f}", "RG")
        self._w(f"{w * PT_PER_MM:.3f}", "w")
        if dash:
            vals = [float(v) * PT_PER_MM
                    for v in str(dash).replace(",", " ").split()]
            self._w("[" + " ".join(f"{v:.2f}" for v in vals) + "]", "0", "d")
        else:
            self._w("[]", "0", "d")
        if cap is not None:
            self._w(str({"butt": 0, "round": 1, "square": 2}.get(cap, 0)), "J")
        if join is not None:
            self._w(str({"miter": 0, "round": 1, "bevel": 2}.get(join, 0)), "j")

    def _fill_state(self, colour):
        r, g, b = self._rgb(colour)
        self._w(f"{r:.3f}", f"{g:.3f}", f"{b:.3f}", "rg")

    @staticmethod
    def _font(bold, mono):
        if mono:
            return "Courier"
        return "Helvetica-Bold" if bold else "Helvetica"

    # -- primitives
    def line(self, x1, y1, x2, y2, w=W_THIN, colour=INK, dash=None):
        self._stroke_state(w, colour, dash, cap="butt")
        self._w(f"{self._x(x1):.2f}", f"{self._y(y1):.2f}", "m")
        self._w(f"{self._x(x2):.2f}", f"{self._y(y2):.2f}", "l", "S")

    def polyline(self, pts, w=W_THIN, colour=INK, dash=None, cap="round",
                 join="round", close=False, fill=None):
        if len(pts) < 2:
            return
        self._stroke_state(w, colour, dash, cap=cap, join=join)
        if fill:
            self._fill_state(fill)
        self._w(f"{self._x(pts[0][0]):.2f}", f"{self._y(pts[0][1]):.2f}", "m")
        for q in pts[1:]:
            self._w(f"{self._x(q[0]):.2f}", f"{self._y(q[1]):.2f}", "l")
        if close:
            self._w("h")
        self._w("B" if fill else "S")

    def rect(self, x0, y0, x1, y1, w=W_THIN, colour=INK, fill=None):
        self._stroke_state(max(w, 0.0), colour, cap="butt", join="miter")
        if fill:
            self._fill_state(fill)
        px, py = self._x(x0), self._y(y1)
        self._w(f"{px:.2f}", f"{py:.2f}", f"{(x1 - x0) * PT_PER_MM:.2f}",
                f"{(y1 - y0) * PT_PER_MM:.2f}", "re")
        self._w("B" if (fill and w > 0) else ("f" if fill else "S"))

    def circle(self, x, y, r, w=W_THIN, colour=INK, fill=None):
        self._stroke_state(w, colour, cap="round", join="round")
        if fill:
            self._fill_state(fill)
        cx, cy, rr = self._x(x), self._y(y), r * PT_PER_MM
        k = self.KAPPA * rr
        self._w(f"{cx + rr:.2f}", f"{cy:.2f}", "m")
        for (x1, y1, x2, y2, x3, y3) in (
                (cx + rr, cy + k, cx + k, cy + rr, cx, cy + rr),
                (cx - k, cy + rr, cx - rr, cy + k, cx - rr, cy),
                (cx - rr, cy - k, cx - k, cy - rr, cx, cy - rr),
                (cx + k, cy - rr, cx + rr, cy - k, cx + rr, cy)):
            self._w(f"{x1:.2f}", f"{y1:.2f}", f"{x2:.2f}", f"{y2:.2f}",
                    f"{x3:.2f}", f"{y3:.2f}", "c")
        self._w("B" if fill else "S")

    def text(self, x, y, s, size=2.5, anchor="start", bold=False, colour=INK,
             mono=False):
        s = str(s)
        if not s:
            return
        font = self._font(bold, mono)
        size_pt = size * PT_PER_MM
        px, py = self._x(x), self._y(y)
        if anchor in ("middle", "end"):
            w = text_width(s, font, size_pt)
            px -= w / 2.0 if anchor == "middle" else w
        self._fill_state(colour)
        self._w("BT", f"/{_FONT_IDS[font]}", f"{size_pt:.2f}", "Tf")
        self._w(f"{px:.2f}", f"{py:.2f}", "Td")
        self._cur += b"(" + _pdf_escape(s) + b") Tj\n"
        self._w("ET")

    def page_break(self):
        self._pages.append(self._cur)
        self._cur = bytearray()

    def width_of(self, s, size, bold=False, mono=False):
        return text_width(s, self._font(bold, mono), size * PT_PER_MM) * MM_PER_PT

    # -- ecriture du fichier
    def save(self):
        pages = list(self._pages)
        if self._cur.strip():
            pages.append(self._cur)
        if not pages:
            pages = [bytearray()]

        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)                    # numero d'objet, 1-based

        catalog = add(b"")                         # reserve, rempli plus bas
        pages_obj = add(b"")
        fonts = {
            "F1": add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                      b"/Encoding /WinAnsiEncoding >>"),
            "F2": add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                      b"/Encoding /WinAnsiEncoding >>"),
            "F3": add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier "
                      b"/Encoding /WinAnsiEncoding >>"),
        }
        res = ("<< /Font << " + " ".join(f"/{k} {v} 0 R" for k, v in fonts.items())
               + " >> /ProcSet [/PDF /Text] >>").encode("ascii")

        media = (f"[0 0 {self.width * PT_PER_MM:.2f} "
                 f"{self.height * PT_PER_MM:.2f}]").encode("ascii")
        page_ids: list[int] = []
        for content in pages:
            data = bytes(content)
            if self.compress:
                data = zlib.compress(data, 6)
                head = (f"<< /Length {len(data)} /Filter /FlateDecode >>"
                        ).encode("ascii")
            else:
                head = f"<< /Length {len(data)} >>".encode("ascii")
            stream = head + b"\nstream\n" + data + b"\nendstream"
            cid = add(stream)
            page_ids.append(add(
                b"<< /Type /Page /Parent " + str(pages_obj).encode() + b" 0 R "
                b"/MediaBox " + media + b" /Resources " + res +
                b" /Contents " + str(cid).encode() + b" 0 R >>"))

        objects[pages_obj - 1] = (
            b"<< /Type /Pages /Count " + str(len(page_ids)).encode() + b" /Kids ["
            + b" ".join(f"{i} 0 R".encode() for i in page_ids) + b"] >>")
        objects[catalog - 1] = (b"<< /Type /Catalog /Pages "
                                + str(pages_obj).encode() + b" 0 R >>")
        info = add(b"<< /Title (" + _pdf_meta(self.title) + b") /Author ("
                   + _pdf_meta(self.author) + b") /Subject ("
                   + _pdf_meta(self.subject) + b") /Creator (tubeiso) "
                   b"/Producer (tubeiso) >>")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for i, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
        out += b"0000000000 65535 f \n"
        for off in offsets[1:]:
            out += f"{off:010d} 00000 n \n".encode("ascii")
        out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R "
                f"/Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode("ascii")

        from pathlib import Path as _Path
        target = _Path(self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(out))
        return target


def ellipsis(sheet: Sheet, s: str, max_mm: float, size: float, bold=False,
             mono=False) -> str:
    """Tronque proprement un texte trop long pour sa colonne.

    Un texte qui deborde sur la colonne voisine rend un plan illisible ; mieux
    vaut une coupure visible qu'un chevauchement.
    """
    s = str(s)
    if sheet.width_of(s, size, bold, mono) <= max_mm:
        return s
    while s and sheet.width_of(s + "…", size, bold, mono) > max_mm:
        s = s[:-1]
    return (s + "…") if s else ""


def wrap(sheet: Sheet, s: str, max_mm: float, size: float, bold=False,
         mono=False, limit: int = 4) -> list[str]:
    """Decoupe un texte en lignes qui tiennent dans `max_mm`."""
    words, lines, cur = str(s).split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if sheet.width_of(trial, size, bold, mono) <= max_mm or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
            if len(lines) >= limit:
                break
    if cur and len(lines) < limit:
        lines.append(cur)
    if len(lines) == limit and len(" ".join(lines)) < len(str(s)):
        lines[-1] = ellipsis(sheet, lines[-1] + " …", max_mm, size, bold, mono)
    return lines


__all__ = ["Sheet", "SvgSheet", "PdfSheet", "text_width", "A4_LANDSCAPE",
           "A4_PORTRAIT",
           "INK", "GREY", "LIGHT", "BODY", "RED", "ORANGE", "GREEN", "BLUE",
           "W_FRAME", "W_OUTLINE", "W_THIN", "W_HAIR", "ellipsis", "wrap"]
