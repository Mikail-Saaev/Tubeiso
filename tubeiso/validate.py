"""Controles qualite, d'apres les regles reelles de la machine.

Chaque controle porte la reference de sa source dans la documentation de
formation ou dans le fichier Archivage Crippa.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import bsa, geometry
from .geometry import Centerline
from .model import Tooling, TubeProgram

ERROR, WARN, INFO = "erreur", "alerte", "info"


@dataclass
class Issue:
    level: str
    code: str
    message: str
    source: str = ""

    def __str__(self) -> str:
        ref = f"  [{self.source}]" if self.source else ""
        return f"[{self.level:7s}] {self.code:20s} {self.message}{ref}"


def check(tube: TubeProgram, tooling: Tooling,
          centerline: Centerline | None = None,
          recut: float = 0.0, length_tol: float = 1.0) -> list[Issue]:
    out: list[Issue] = []
    d = int(tube.diameter) if tube.diameter else 0

    for w in tube.warnings:
        lvl = ERROR if ("negatif" in w or "incomplet" in w) else WARN
        out.append(Issue(lvl, "parseur", w))

    if not tube.complete:
        out.append(Issue(ERROR, "programme_tronque",
                         "pas de M30 : geometrie inexploitable", "255 car."))
        return out
    if d not in bsa.RM:
        out.append(Issue(ERROR, "diametre_inconnu",
                         f"Ø{tube.diameter:g} absent des tables BSA", "DOC p.4"))
        return out

    # --- bouclage : le seul juge de paix
    r15 = [b.angle for b in tube.bends]
    calc = bsa.developed_length(tube.straights, r15, d, recut)
    if tube.declared_length:
        delta = calc - tube.declared_length
        out.append(Issue(INFO if abs(delta) <= length_tol else ERROR, "bouclage_R6",
                         f"R6 recalcule {calc:.1f} / declare "
                         f"{tube.declared_length:.0f} ({delta:+.2f} mm)",
                         "XLSM Feuil2"))

    L = tube.declared_length or calc
    if L < bsa.MIN_DEVELOPED:
        out.append(Issue(ERROR, "developpe_court",
                         f"{L:.0f} mm < {bsa.MIN_DEVELOPED:.0f} mm", "DOC 2.2"))
    elif L < bsa.RECOMMENDED_DEVELOPED:
        out.append(Issue(WARN, "developpe_court",
                         f"{L:.0f} mm < {bsa.RECOMMENDED_DEVELOPED:.0f} recommandes",
                         "DOC 2.2"))
    if L > bsa.MAX_DEVELOPED:
        out.append(Issue(ERROR, "developpe_long",
                         f"{L:.0f} mm > {bsa.MAX_DEVELOPED:.0f} mm : splitter",
                         "DOC 2.2"))

    # --- segments droits intermediaires : largeur des mors
    mini = bsa.MIN_STRAIGHT.get(d)
    for i, s in enumerate(tube.straights[1:-1], start=1):
        if s < 0:
            out.append(Issue(ERROR, "droite_negative", f"segment {i + 1} = {s:.1f} mm"))
        elif mini and s < mini:
            out.append(Issue(ERROR, "droite_trop_courte",
                             f"segment {i + 1} = {s:.1f} mm < {mini} mm (mors)",
                             "DOC 5.5"))

    # --- regles de decharge
    if len(tube.straights) >= 2:
        last, before = tube.straights[-1], tube.straights[-2]
        somme = last + before + recut
        seuil = bsa.MIN_LAST_TWO.get(d)
        if seuil and somme < seuil:
            out.append(Issue(ERROR, "reglette_collision",
                             f"2 derniers segments = {somme:.1f} mm < {seuil} mm : "
                             "faire une recoupe", "DOC 5.1"))
        mini_last = bsa.MIN_LAST.get(d)
        if mini_last and last + recut < mini_last - 0.5:   # Y arrondis a 0.5
            out.append(Issue(ERROR, "dernier_segment_court",
                             f"{last + recut:.1f} mm < {mini_last} mm",
                             "XLSM Feuil2!B30"))
        if last > bsa.MAX_LAST:
            out.append(Issue(WARN, "dernier_segment_long",
                             f"{last:.1f} mm > {bsa.MAX_LAST:.0f} mm : "
                             "prevoir un faux pli a 0°", "DOC 5.3"))

    # --- angles
    for i, b in enumerate(tube.bends, start=1):
        if b.angle <= 0:
            out.append(Issue(WARN, "angle_nul", f"coude {i} : {b.angle:g}°"))
        elif b.angle >= 180:
            out.append(Issue(WARN, "cintrage_180",
                             f"coude {i} : {b.angle:g}° — sequence de degagement",
                             "DOC 5.7"))

    # --- R7=0 avant l'avant-dernier pli
    if tube.params.get("R7") == 2 and len(tube.bends) >= 2:
        seuil = bsa.MIN_LAST_TWO.get(d)
        if seuil and (tube.straights[-1] + tube.straights[-2]) < seuil:
            out.append(Issue(WARN, "r7_manquant",
                             "inserer R7=0 apres l'avant-dernier pli",
                             "DOC 4.2"))

    # --- collision de la piece sur elle-meme
    if centerline is not None and tube.diameter:
        dist = geometry.min_segment_distance(
            centerline, tube.diameter, tooling.clr or 0.0)
        if dist < tube.diameter:
            out.append(Issue(ERROR, "auto_collision",
                             f"rapprochement {dist:.1f} mm < Ø{tube.diameter:g}"))
        elif dist < 2 * tube.diameter:
            out.append(Issue(WARN, "passage_serre", f"rapprochement {dist:.1f} mm"))
    return out


def worst(issues: list[Issue]) -> str:
    if any(i.level == ERROR for i in issues):
        return ERROR
    if any(i.level == WARN for i in issues):
        return WARN
    return INFO
