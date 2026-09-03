"""Constantes machine et modele de longueur BSA / Crippa.

Toutes les valeurs de ce module sont sourcees. Elles ne sont plus devinees.

  [DOC]   FORMATION_CRIPPA_002, tableaux p.4, 16-18, 33
  [XLSM]  archivage_crippa_EDT_09_1.xlsm, Feuil2 (formules de calcul)

Modele de longueur, transcription litterale de Feuil2 :

    arcs      = 0.0174444 * somme(R15) * Rm            (B2, B3, B6, B4)
    Lg_theo   = arcs + somme(Y) + recoupe              (B7)
    R6        = Lg_theo - arcs * allongement% / 100    (H7, "Lg. reel tube")

somme(Y) inclut R12 (premier segment) et le dernier segment, qui n'est PAS
ecrit dans le programme : il s'en deduit. Le commentaire DS le rappelle, mais
il n'est ni systematique ni fiable — l'expert BSA a explicitement dit de ne
pas s'en servir comme reference.
"""
from __future__ import annotations

import math

# Leur approximation de pi/180. On la garde telle quelle pour reproduire
# exactement les valeurs de l'outil Excel (ecart relatif 5e-5).
K_DEG = 0.0174444444444

# [DOC p.4] Rm = rayon de cintrage, mm. Egalement recopie dans R41.
RM = {4: 11, 6: 11, 8: 14, 10: 23, 12: 30, 15: 45, 16: 45, 18: 52, 22: 86}

# [XLSM Feuil2!B8] Coefficient d'allongement, en % de la longueur d'arc.
ELONGATION_PCT = {4: 3, 6: 5, 8: 5, 10: 5, 12: 5, 15: 4, 16: 4, 18: 4}

# [DOC p.4] Elasticite (retour elastique) et position axe Z de charge (R33).
ELASTICITY_PCT = {4: 2, 6: 2, 8: 2, 10: 2, 12: 2, 15: 3, 16: 3, 18: 4, 22: 6}
R33_LOAD_Z = {4: 280, 6: 280, 8: 279.5, 10: 279.5, 12: 279,
              15: 279, 16: 278.5, 18: 278.5, 22: 278}

# [DOC 5.4] R15 a programmer pour obtenir un pli reel a 90 degres.
R15_FOR_90 = {4: 93, 6: 92, 8: 92, 10: 92, 12: 92.5, 15: 92.5, 18: 93}

# [DOC 5.5 / XLSM] Segment droit intermediaire minimum = largeur des mors.
MIN_STRAIGHT = {4: 7, 6: 11, 8: 15, 10: 20, 12: 24, 15: 30, 16: 30, 18: 36}

# [DOC 5.1 / XLSM Feuil2!B11] Somme minimale des 2 derniers segments (reglette).
MIN_LAST_TWO = {4: 90, 6: 90, 8: 120, 10: 120, 12: 170, 15: 170, 16: 170, 18: 170}

# [XLSM Feuil2!B30] Longueur minimale du dernier segment.
MIN_LAST = {4: 35, 6: 35, 8: 45, 10: 53, 12: 70, 15: 70, 16: 70, 18: 77}

# [DOC 5.3] Au-dela, faire un faux pli a 0 degre.
MAX_LAST = 450.0

# [DOC 2.2] Bornes de longueur developpee.
MIN_DEVELOPED, RECOMMENDED_DEVELOPED, MAX_DEVELOPED = 180.0, 200.0, 2300.0

# [DOC 5.8] Course X minimale selon la tete.
X_MIN_LOWER = {4: 47, 6: 47, 8: 47, 10: 36}
X_MIN_UPPER = {4: 67, 6: 67, 8: 67, 10: 55}

# [DOC 4.2] Codes d'embout de la LFT.
END_FITTINGS = {
    "V04": "Sertissage Vogel Ø4", "V06": "Sertissage Vogel Ø6",
    "V08": "Sertissage Vogel Ø8", "V10": "Sertissage Vogel Ø10",
    "PE": "Pincage perpendiculaire Ø6", "PA": "Pincage parallele Ø6",
}

# [DOC 4.2] Segmentation du programme en deux moities : premier Y ecrit = 35 max.
SPLIT_Y = 35.0

HEAD_UPPER, HEAD_LOWER = 4, 5
HEAD_NAMES = {HEAD_UPPER: "tete du haut", HEAD_LOWER: "tete du bas"}


def parse_tooling(code: str) -> tuple[int, int]:
    """'L54' -> (tete 5, Ø4). 'L412' -> (tete 4, Ø12). [DOC 4.2]"""
    digits = code.lstrip("Ll")
    if len(digits) < 2:
        raise ValueError(f"code outillage illisible : {code}")
    head = int(digits[0])
    if head not in (HEAD_UPPER, HEAD_LOWER):
        raise ValueError(f"tete inconnue dans {code} (attendu 4 ou 5)")
    return head, int(digits[1:])


def arc_length(angles_deg, diameter: float) -> float:
    """Longueur cumulee des arcs. [XLSM B4]"""
    rm = RM.get(int(diameter))
    if rm is None:
        raise KeyError(f"pas de rayon Rm connu pour Ø{diameter:g}")
    return K_DEG * sum(angles_deg) * rm


def developed_length(straights, angles_deg, diameter: float,
                     recut: float = 0.0) -> float:
    """R6 attendu pour une geometrie donnee. [XLSM B7 puis H7]"""
    arcs = arc_length(angles_deg, diameter)
    theo = arcs + sum(straights) + recut
    return theo - arcs * ELONGATION_PCT[int(diameter)] / 100.0


def last_straight(r6: float, known_straights, angles_deg, diameter: float,
                  recut: float = 0.0) -> float:
    """Deduit le dernier segment droit de R6. C'est la valeur a utiliser,
    pas le DS du commentaire."""
    arcs = arc_length(angles_deg, diameter)
    net = arcs * (1.0 - ELONGATION_PCT[int(diameter)] / 100.0)
    return r6 - sum(known_straights) - recut - net


def true_angle(r15: float, diameter: float) -> float:
    """Angle geometrique reel si R15 porte une compensation d'elasticite.

    [DOC 5.4] La compensation n'est PAS systematique : le flux normal reporte
    directement l'angle mesure dans Catia. On expose la conversion pour les
    programmes qui la portent, mais on ne l'applique pas par defaut.
    """
    ref = R15_FOR_90.get(int(diameter))
    return r15 if ref is None else r15 * 90.0 / ref


def bend_radius(diameter: float) -> float:
    return float(RM[int(diameter)])


def rotation_sign(head: int) -> int:
    """[DOC 8.3.5] Ø4-10 sur tete du bas : horaire = +. Ø12-18 sur tete du
    haut : antihoraire = -."""
    return 1 if head == HEAD_LOWER else -1


def split_moves(segment: float) -> list[float]:
    """Decoupe un segment en mouvements Y comme le fait le programmeur.
    [XLSM Feuil1!E20 : =IF(Y>35, Y-35, "")]"""
    if segment <= SPLIT_Y:
        return [segment]
    return [SPLIT_Y, round(segment - SPLIT_Y, 2)]


__all__ = [n for n in dir() if not n.startswith("_") and n != "math"]
