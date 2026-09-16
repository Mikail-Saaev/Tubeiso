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

# [DOC p.4] Designation commerciale du tube : diametre exterieur / interieur.
# La paroi s'en deduit, et c'est elle qui rend le solide exporte creux.
BORE = {4: 2.5, 6: 4, 8: 6, 10: 8, 12: 9, 15: 12, 16: 13, 18: 14, 22: 17}
WALL = {d: round((d - b) / 2.0, 3) for d, b in BORE.items()}

# [DOC p.4] Elasticite (retour elastique) et position axe Z de charge (R33).
ELASTICITY_PCT = {4: 2, 6: 2, 8: 2, 10: 2, 12: 2, 15: 3, 16: 3, 18: 4, 22: 6}
R33_LOAD_Z = {4: 280, 6: 280, 8: 279.5, 10: 279.5, 12: 279,
              15: 279, 16: 278.5, 18: 278.5, 22: 278}

# [DOC 5.4] R15 a programmer pour obtenir un pli reel a 90 degres.
# Le O16 ne figure pas dans la table : il partage la matrice du O15 (Rm=45),
# donc sa compensation. Sans cette ligne, un O16 sortait sans aucune
# correction d'elasticite, soit 2.5 deg de trop sur chaque coude.
R15_FOR_90 = {4: 93, 6: 92, 8: 92, 10: 92, 12: 92.5, 15: 92.5, 16: 92.5,
              18: 93}

# [DOC 5.5 / XLSM] Segment droit intermediaire minimum = largeur des mors.
# [XLSM Feuil2!B47] donne 36 mm pour le O16, et non 30 comme le O15.
MIN_STRAIGHT = {4: 7, 6: 11, 8: 15, 10: 20, 12: 24, 15: 30, 16: 36, 18: 36}

# [DOC 5.1 / XLSM Feuil2!B11] Somme minimale des 2 derniers segments (reglette).
MIN_LAST_TWO = {4: 90, 6: 90, 8: 120, 10: 120, 12: 170, 15: 170, 16: 170, 18: 170}

# [XLSM Feuil2!B30] Longueur minimale du dernier segment.
MIN_LAST = {4: 35, 6: 35, 8: 45, 10: 53, 12: 70, 15: 70, 16: 70, 18: 77}

# [DOC 5.3] Au-dela, faire un faux pli a 0 degre.
MAX_LAST = 450.0

# [DOC 3.2, tableau des vitesses] Course de l'axe C : 0 a 188 degres. C'est la
# borne machine reelle, pas une convention.
MAX_BEND_ANGLE = 188.0

# [DOC 2.2] Bornes de longueur developpee.
MIN_DEVELOPED, RECOMMENDED_DEVELOPED, MAX_DEVELOPED = 180.0, 200.0, 2300.0

# [DOC 5.8] Course X minimale selon la tete.
X_MIN_LOWER = {4: 47, 6: 47, 8: 47, 10: 36}
X_MIN_UPPER = {4: 67, 6: 67, 8: 67, 10: 55}

# [DOC 4.2] Codes d'embout de la LFT.
END_FITTINGS = {
    "V04": "Sertissage Vögel Ø4", "V06": "Sertissage Vögel Ø6",
    "V08": "Sertissage Vögel Ø8", "V10": "Sertissage Vögel Ø10",
    "PE": "Pinçage perpendiculaire Ø6", "PA": "Pinçage parallèle Ø6",
}

# [DOC 8.1.2] Forages Vogel (SKF), DIN 3854 / DIN 3862, tubes sans soudure.
# diametre exterieur -> (designation du forage, T1 profondeur mm, D3 mm, filetage)
# T1 est la profondeur d'emmanchement du tube : elle conditionne la longueur
# utile du premier et du dernier segment. « Ces profondeurs sont a controler
# sur chaque tube dans la maquette 3D pour assurer les longueurs du premier et
# dernier segment, s'ils sont sertis. »
VOGEL_DRILL = {
    2.5: ("1102", 8.5, 1.5, "M6x0,75"),
    4: ("1404", 12.5, 3.0, "M8x1"),
    6: ("1406", 14.0, 4.5, "M10x1"),
    8: ("1408", 18.5, 6.5, "M14x1,5"),
    10: ("1410", 19.5, 8.5, "M16x1,5"),
    12: ("1412", 22.0, 10.5, "M18x1,5"),
}

# [DOC 8.1.2] Profondeur du tube dans un raccord Ermeto EO 24 degres PARKER.
# diametre exterieur -> (serie L legere, serie S lourde). None = non disponible.
# La cote T1 est identique a la longueur de l'ecrou.
ERMETO_INSERT = {
    6: (14.5, 16.5), 8: (14.5, 16.5), 10: (15.5, 17.5), 12: (15.5, 17.5),
    15: (17.0, None), 18: (18.0, None), 22: (20.0, None), 28: (21.0, None),
    35: (24.0, None),
}

# [DOC 8.3.3] Les cotes relevees dans CATIA sont arrondies au demi-millimetre,
# et les angles au degre. C'est la tolerance de lecture du modele, a ne pas
# confondre avec une tolerance de fabrication.
Y_ROUNDING = 0.5
ANGLE_ROUNDING = 1.0

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


def arc_length(angles_deg, diameter: float, rm: float | None = None) -> float:
    """Longueur cumulee des arcs. [XLSM B4]

    `rm` permet d'imposer le rayon configure dans les reglages. Sans lui, on
    reprend la table BSA — mais alors un Rm modifie par l'utilisateur ne
    s'appliquerait qu'a la 3D et pas au calcul de longueur, ce qui rendrait
    les deux incoherents.
    """
    if rm is None:
        rm = RM.get(int(diameter))
    if rm is None:
        raise KeyError(f"pas de rayon Rm connu pour Ø{diameter:g}")
    return K_DEG * sum(angles_deg) * float(rm)


def developed_length(straights, angles_deg, diameter: float,
                     recut: float = 0.0, rm: float | None = None,
                     elongation: float | None = None) -> float:
    """R6 attendu pour une geometrie donnee. [XLSM B7 puis H7]"""
    arcs = arc_length(angles_deg, diameter, rm)
    theo = arcs + sum(straights) + recut
    e = ELONGATION_PCT[int(diameter)] if elongation is None else elongation
    return theo - arcs * e / 100.0


def last_straight(r6: float, known_straights, angles_deg, diameter: float,
                  recut: float = 0.0, rm: float | None = None,
                  elongation: float | None = None) -> float:
    """Deduit le dernier segment droit de R6. C'est la valeur a utiliser,
    pas le DS du commentaire."""
    arcs = arc_length(angles_deg, diameter, rm)
    e = ELONGATION_PCT[int(diameter)] if elongation is None else elongation
    net = arcs * (1.0 - e / 100.0)
    return r6 - sum(known_straights) - recut - net


# --------------------------------------------------------------- retour elastique
#
# [DOC 5.4] « Du a son elasticite, lors du cintrage du tube, la valeur angulaire
# de cintrage doit etre superieure afin d'avoir au final un angle a 90 degres. »
# La table R15_FOR_90 donne le R15 a programmer pour obtenir 90 degres reels ;
# le supplement vaut donc R15_FOR_90 - 90, soit 3 degres en O4, 2 en O6/8/10,
# 2.5 en O12/15, 3 en O18.
#
# « Pour un angle de cintrage de 45 degres, divise par deux env. l'angle
# additionnel pour l'elasticite. Exemple pour du tube 10/8 => R15=46. »
# Le supplement est donc PROPORTIONNEL a l'angle : supplement = k * angle,
# avec k = (R15_FOR_90 - 90) / 90.
#
# En pratique le programmeur mesure un angle dans Catia, calcule ce supplement,
# l'ARRONDIT, et ecrit la somme. C'est ce que dit l'annotation du 412 :
# « R15=46, en realite 45 degres mais faut ajouter 1 degre pour elasticite ».
# Pour retrouver l'angle reel il faut donc inverser cet arrondi, pas seulement
# diviser : c'est le mode "entier" ci-dessous, qui restitue des angles ronds
# (90, 45, 43...) la ou la division donnerait 90.0, 45.0, 43.04...

SPRINGBACK_MAX = 6          # supplement maximal envisage, en degres


def _round_half_up(x: float) -> float:
    """Arrondi d'atelier : 0.5 monte. round() de Python arrondit au pair le
    plus proche (round(0.5) == 0), ce qui n'est pas ce que fait un programmeur."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def springback_rate(diameter: float) -> float:
    """Supplement angulaire par degre de pli. 0 si le diametre est inconnu."""
    ref = R15_FOR_90.get(int(diameter))
    return 0.0 if ref is None else (ref - 90.0) / 90.0


def springback_supplement(true_deg: float, diameter: float,
                          rounded: bool = True) -> float:
    """Supplement a AJOUTER a un angle reel pour obtenir le R15 a programmer.

    C'est le sens dans lequel travaille le programmeur BSA.
    """
    raw = springback_rate(diameter) * true_deg
    return _round_half_up(raw) if rounded else raw


def programmed_angle(true_deg: float, diameter: float) -> float:
    """Angle reel -> R15 a ecrire dans le programme. [DOC 5.4]"""
    return true_deg + springback_supplement(true_deg, diameter)


def real_angle(r15: float, diameter: float, mode: str = "entier"
               ) -> tuple[float, float]:
    """R15 lu dans le programme -> (angle reel, supplement retire).

    mode == "entier"       inverse l'arrondi du programmeur : on cherche l'angle
                           entier qui, augmente de son supplement arrondi,
                           redonne exactement le R15 ecrit. C'est le defaut.
    mode == "proportionnel" applique r15 * 90 / R15_FOR_90, sans arrondi.
    mode == "brut"         aucune correction ; R15 est pris pour l'angle reel.

    Retourne toujours un couple, pour que l'interface puisse afficher les deux
    valeurs cote a cote : ce qui est programme et ce qui sort de la machine.
    """
    d = int(diameter) if diameter else 0
    rate = springback_rate(d)
    if mode == "brut" or rate == 0.0 or r15 <= 0:
        return float(r15), 0.0

    proportional = r15 * 90.0 / R15_FOR_90[d]
    if mode == "proportionnel":
        return proportional, r15 - proportional

    # Mode entier : on cherche l'angle que le programmeur a mesure dans CATIA.
    # Il travaille au demi-degre [DOC 8.3.3, « arrondir les cotes a 0.5 mm » et
    # table 5.4 qui donne des R15 de 92.5], donc les candidats vont de demi en
    # demi. S'en tenir aux entiers laissait sans reponse un coude sur dix du
    # parc — R15=92.5 en O8, 25.5 en O6, 46 en O4 — qui basculaient alors sur
    # une division produisant des angles comme 90.489 deg.
    step = 0.5
    candidates = []
    n = int(SPRINGBACK_MAX / step) + 1
    for k in range(n + 1):
        theta = r15 - k * step
        if theta <= 0:
            break
        delta = r15 - theta
        # Le supplement est arrondi « a la main » : la doc dit « divise par deux
        # ENV. l'angle additionnel », et l'annotation du 412 montre 1 deg la ou
        # le calcul donne 1.02. On accepte donc l'arrondi par defaut comme par
        # exces, et on tranche ensuite par la proximite a la valeur exacte.
        raw = rate * theta
        if math.floor(raw) - 1e-9 <= delta <= math.ceil(raw) + 1e-9:
            # Un angle entier l'emporte toujours sur un demi-degre : le
            # demi-degre n'est retenu que lorsque AUCUN entier ne reproduit le
            # R15 ecrit, comme R15=92.5 en O8. A egalite, on garde le
            # supplement le plus fort, puisque le programmeur en applique un.
            is_half = abs(theta - round(theta)) > 1e-9
            candidates.append((is_half, round(abs(theta - proportional), 6),
                               -delta, float(theta), float(delta)))
    if not candidates:
        # aucun angle ne reproduit ce R15 : on ne force rien, on divise
        return proportional, r15 - proportional
    candidates.sort()
    return candidates[0][3], candidates[0][4]


def true_angle(r15: float, diameter: float, mode: str = "entier") -> float:
    """Angle geometrique reel, sans le supplement d'elasticite."""
    return real_angle(r15, diameter, mode)[0]


ANGLE_MODES = ("entier", "proportionnel", "brut")
DEFAULT_ANGLE_MODE = "entier"


def bend_radius(diameter: float) -> float:
    return float(RM[int(diameter)])


def rotation_sign(head: int) -> int:
    """OBSOLETE, conservee pour compatibilite. Ne plus utiliser.

    Le [DOC 8.3.5] dit que pour un demi-tour on ECRIT +180 en tete du bas et
    -180 en tete du haut. Ce sont deux facons d'ecrire la MEME rotation, un
    choix de trajectoire pour eviter une collision — pas une inversion de
    l'axe B. Le meme chapitre donne d'ailleurs un -90 en tete du bas.

    Multiplier B par -1 selon la tete produisait donc une piece MIROIR pour
    toute la famille O12-O18. Le sens global se regle par `handedness`, une
    seule fois, et se verifie sur une piece reelle.
    """
    return 1


def split_moves(segment: float) -> list[float]:
    """Decoupe un segment en mouvements Y comme le fait le programmeur.
    [XLSM Feuil1!E20 : =IF(Y>35, Y-35, "")]"""
    if segment <= SPLIT_Y:
        return [segment]
    return [SPLIT_Y, round(segment - SPLIT_Y, 2)]


__all__ = [n for n in dir() if not n.startswith("_") and n != "math"]
