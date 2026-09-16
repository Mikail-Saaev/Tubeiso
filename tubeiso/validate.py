"""Controles qualite, d'apres les regles reelles de la machine.

Chaque controle porte la reference de sa source dans la documentation de
formation ou dans le fichier Archivage Crippa.

Un mot sur le controle de longueur. Jusqu'a la v4, on comparait le developpe
recalcule a R6 — mais le dernier segment etait justement DEDUIT de R6 par la
meme equation. Le calcul se simplifiait, l'ecart valait exactement zero sur
toutes les pieces, et le controle ne pouvait donc rien detecter. Il est
remplace ici par les deux seuls temoins reellement independants du calcul :

    DS         le dernier segment ecrit dans le commentaire du programme
    LONGUEUR   la longueur portee par la LFT, quand elle differe de R6
"""
from __future__ import annotations

from dataclasses import dataclass

from . import bsa, geometry
from .geometry import Centerline
from .model import Tooling, TubeProgram

ERROR, WARN, INFO = "erreur", "alerte", "info"

# DS est ecrit au millimetre et les Y sont arrondis a 0.5 mm : en dessous de
# ce seuil, un ecart ne prouve rien.
DS_ROUNDING = 0.75
# Au-dela, l'ecart ne s'explique plus par l'arrondi : c'est soit un DS perime
# (cas connu du repere 412), soit un modele de longueur faux.
DS_SUSPECT = 2.0


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
          recut: float = 0.0, length_tol: float = 1.0,
          lft_length: float | None = None) -> list[Issue]:
    out: list[Issue] = []
    d = int(tube.diameter) if tube.diameter else 0

    for w in tube.warnings:
        if ("retour elastique" in w or "reconstitue depuis" in w
                or "faux pli" in w):
            lvl = INFO
        elif "negatif" in w or "incomplet" in w:
            lvl = ERROR
        else:
            lvl = WARN
        out.append(Issue(lvl, "parseur", w))

    # --- tube droit : quelques controles seulement
    if tube.straight:
        L = sum(tube.straights)
        if L <= 0:
            out.append(Issue(ERROR, "longueur_absente",
                             "tube droit sans longueur exploitable"))
        else:
            out.append(Issue(INFO, "tube_droit",
                             f"tube droit de {L:.0f} mm, aucun cintrage"))
        return out

    if not tube.complete:
        out.append(Issue(ERROR, "programme_tronque",
                         "pas de M30 : geometrie inexploitable", "255 car."))
        return out
    if d not in bsa.RM:
        out.append(Issue(ERROR, "diametre_inconnu",
                         f"Ø{tube.diameter:g} absent des tables BSA", "DOC p.4"))
        return out

    # --- controle de longueur, sur temoins independants
    _check_length(out, tube, recut, lft_length)

    L = tube.declared_length or sum(tube.straights)
    if L < bsa.MIN_DEVELOPED:
        out.append(Issue(ERROR, "developpe_court",
                         f"{L:.0f} mm < {bsa.MIN_DEVELOPED:.0f} mm", "DOC 2.2"))
    elif L < bsa.RECOMMENDED_DEVELOPED:
        out.append(Issue(WARN, "developpe_court",
                         f"{L:.0f} mm < {bsa.RECOMMENDED_DEVELOPED:.0f} recommandes "
                         f"(rajouter {bsa.RECOMMENDED_DEVELOPED - L:.0f} mm)",
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
            # Le faux pli a 0 degre est la parade prevue par la doc. S'il est
            # deja dans le programme, la regle est respectee : le signaler
            # serait reprocher au programmeur d'avoir bien fait.
            if tube.false_bends:
                out.append(Issue(INFO, "faux_pli_present",
                                 f"dernier segment de {last:.0f} mm, faux pli à 0° "
                                 "présent dans le programme", "DOC 5.3"))
            else:
                out.append(Issue(WARN, "dernier_segment_long",
                                 f"{last:.1f} mm > {bsa.MAX_LAST:.0f} mm : "
                                 "prévoir un faux pli à 0°", "DOC 5.3"))

    # --- angles : on controle le PROGRAMME (R15), pas l'angle reel, car c'est
    # R15 que la machine execute et que borne la course de l'axe C.
    for i, b in enumerate(tube.bends, start=1):
        r15 = b.r15 if b.r15 is not None else b.angle
        if r15 <= 0:
            out.append(Issue(WARN, "angle_nul", f"coude {i} : {r15:g}°"))
        elif r15 > bsa.MAX_BEND_ANGLE:
            out.append(Issue(ERROR, "angle_hors_course",
                             f"coude {i} : R15={r15:g}° > {bsa.MAX_BEND_ANGLE:g}° "
                             "de course de l'axe C", "DOC 3.2"))
        elif r15 >= 180:
            out.append(Issue(WARN, "cintrage_180",
                             f"coude {i} : R15={r15:g}° — sequence de degagement "
                             "de tete obligatoire", "DOC 5.7"))

    # --- R7=0 avant l'avant-dernier pli
    # [XLSM Feuil2!B11-B15] =IF(dernier segment + recoupe < longueur reglette,
    # "METTRE R7=0"). Ne pas confondre avec la regle de recoupe [DOC 5.1], qui
    # porte sur la SOMME des deux derniers segments.
    if len(tube.bends) >= 2 and not tube.r7_released:
        seuil = bsa.MIN_LAST_TWO.get(d)
        if seuil and (tube.straights[-1] + recut) < seuil:
            out.append(Issue(WARN, "r7_manquant",
                             f"dernier segment + recoupe = "
                             f"{tube.straights[-1] + recut:.1f} mm < {seuil} mm : "
                             "inserer R7=0 apres l'avant-dernier pli",
                             "XLSM Feuil2!B15"))

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


def _check_length(out: list[Issue], tube: TubeProgram, recut: float,
                  lft_length: float | None) -> None:
    """Les deux temoins independants du modele de longueur."""
    ds = tube.ds
    last = tube.straights[-1] if tube.straights else None

    if ds is not None and last is not None:
        delta = last - ds
        if abs(delta) <= DS_ROUNDING:
            lvl, note = INFO, "dans l'arrondi du DS"
        elif abs(delta) <= DS_SUSPECT:
            lvl, note = WARN, "au-dela de l'arrondi"
        else:
            lvl, note = ERROR, "incoherent"
        out.append(Issue(lvl, "controle_DS",
                         f"dernier segment calcule {last:.2f} mm contre DS={ds:g} "
                         f"({delta:+.2f} mm, {note})", "commentaire programme"))
    elif tube.complete:
        out.append(Issue(WARN, "pas_de_temoin",
                         "aucun DS dans le commentaire : le modele de longueur "
                         "n'est verifie par rien sur cette piece"))

    if lft_length is not None and tube.declared_length is not None:
        delta = lft_length - tube.declared_length
        if abs(delta) > 0.51:
            out.append(Issue(ERROR, "longueur_LFT",
                             f"LONGUEUR={lft_length:g} contredit R6="
                             f"{tube.declared_length:g} ({delta:+.1f} mm)",
                             "colonne LFT"))


def worst(issues: list[Issue]) -> str:
    if any(i.level == ERROR for i in issues):
        return ERROR
    if any(i.level == WARN for i in issues):
        return WARN
    return INFO
