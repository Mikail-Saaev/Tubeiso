"""Surface de dessin en millimetres papier, deux rendus : SVG et PDF.

La mise en plan est ecrite UNE fois, contre cette interface. Elle sort ensuite
en SVG pour l'apercu dans l'application, et en PDF pour le sous-traitant, sans
qu'aucune cote ne puisse diverger entre les deux — c'est tout l'interet.

Repere : origine en haut a gauche, X vers la droite, Y vers le bas, unites en
millimetres reels sur la feuille. C'est la convention du SVG ; l'adaptateur PDF
retourne l'axe Y pour reportlab, qui compte depuis le bas.
"""
from __future__ import annotations

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
        # Approximation suffisante pour du placement : Helvetica tourne autour
        # de 0.52 em de chasse moyenne, un peu plus en gras.
        k = 0.60 if mono else (0.55 if bold else 0.52)
        return len(str(s)) * size * k

    def to_svg(self, page: int = 0) -> str:
        body = "".join(self.pages[page])
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}mm" '
                f'height="{self.height}mm" viewBox="0 0 {self.width} {self.height}">'
                f'<rect width="{self.width}" height="{self.height}" fill="#ffffff"/>'
                f'{body}</svg>')


class PdfSheet(Sheet):
    """Rendu PDF vectoriel via reportlab. Multi-pages."""

    def __init__(self, path, width=A4_LANDSCAPE[0], height=A4_LANDSCAPE[1],
                 title="", author="tubeiso", subject=""):
        super().__init__(width, height)
        try:
            from reportlab.lib.colors import HexColor
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfgen import canvas as rl_canvas
        except ImportError as exc:                                # pragma: no cover
            raise RuntimeError(
                "reportlab est requis pour l'export PDF : pip install reportlab"
            ) from exc
        self._hex = HexColor
        self._metrics = pdfmetrics
        self._c = rl_canvas.Canvas(str(path),
                                   pagesize=(width * PT_PER_MM, height * PT_PER_MM))
        self._c.setTitle(title or "Plan de tube")
        self._c.setAuthor(author)
        self._c.setSubject(subject)
        self._c.setCreator("tubeiso")

    # -- conversions
    def _x(self, v):
        return float(v) * PT_PER_MM

    def _y(self, v):
        return (self.height - float(v)) * PT_PER_MM

    def _pen(self, w, colour, dash=None):
        self._c.setLineWidth(w * PT_PER_MM)
        self._c.setStrokeColor(self._hex(colour))
        if dash:
            pattern = [float(v) * PT_PER_MM for v in str(dash).replace(",", " ").split()]
            self._c.setDash(pattern, 0)
        else:
            self._c.setDash([], 0)

    @staticmethod
    def _font(bold, mono):
        if mono:
            return "Courier-Bold" if bold else "Courier"
        return "Helvetica-Bold" if bold else "Helvetica"

    # -- primitives
    def line(self, x1, y1, x2, y2, w=W_THIN, colour=INK, dash=None):
        self._pen(w, colour, dash)
        self._c.line(self._x(x1), self._y(y1), self._x(x2), self._y(y2))

    def polyline(self, pts, w=W_THIN, colour=INK, dash=None, cap="round",
                 join="round", close=False, fill=None):
        if len(pts) < 2:
            return
        self._pen(w, colour, dash)
        self._c.setLineCap({"round": 1, "butt": 0, "square": 2}.get(cap, 1))
        self._c.setLineJoin({"round": 1, "miter": 0, "bevel": 2}.get(join, 1))
        p = self._c.beginPath()
        p.moveTo(self._x(pts[0][0]), self._y(pts[0][1]))
        for pt in pts[1:]:
            p.lineTo(self._x(pt[0]), self._y(pt[1]))
        if close:
            p.close()
        if fill:
            self._c.setFillColor(self._hex(fill))
        self._c.drawPath(p, stroke=1, fill=1 if fill else 0)
        self._c.setLineCap(0)
        self._c.setLineJoin(0)

    def rect(self, x0, y0, x1, y1, w=W_THIN, colour=INK, fill=None):
        self._pen(w, colour)
        if fill:
            self._c.setFillColor(self._hex(fill))
        self._c.rect(self._x(x0), self._y(y1), self._x(x1 - x0),
                     (y1 - y0) * PT_PER_MM, stroke=1, fill=1 if fill else 0)

    def circle(self, x, y, r, w=W_THIN, colour=INK, fill=None):
        self._pen(w, colour)
        if fill:
            self._c.setFillColor(self._hex(fill))
        self._c.circle(self._x(x), self._y(y), r * PT_PER_MM,
                       stroke=1, fill=1 if fill else 0)

    def text(self, x, y, s, size=2.5, anchor="start", bold=False, colour=INK,
             mono=False):
        self._c.setFont(self._font(bold, mono), size * PT_PER_MM)
        self._c.setFillColor(self._hex(colour))
        px, py, s = self._x(x), self._y(y), str(s)
        if anchor == "middle":
            self._c.drawCentredString(px, py, s)
        elif anchor == "end":
            self._c.drawRightString(px, py, s)
        else:
            self._c.drawString(px, py, s)

    def page_break(self):
        self._c.showPage()

    def width_of(self, s, size, bold=False, mono=False):
        return self._metrics.stringWidth(
            str(s), self._font(bold, mono), size * PT_PER_MM) * MM_PER_PT

    def save(self):
        self._c.save()


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


__all__ = ["Sheet", "SvgSheet", "PdfSheet", "A4_LANDSCAPE", "A4_PORTRAIT",
           "INK", "GREY", "LIGHT", "BODY", "RED", "ORANGE", "GREEN", "BLUE",
           "W_FRAME", "W_OUTLINE", "W_THIN", "W_HAIR", "ellipsis", "wrap"]
