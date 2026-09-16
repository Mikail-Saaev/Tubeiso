"""Catalogue des matieres tube et tuyau BSA.

Source unique : le scan « N deg matiere tube et tuyau » (Cognard Herve,
07.09.2026), complete par la table des valeurs normalisees de la
documentation de formation [DOC 1.2].

Ce module repond a une question et une seule : **ce code matiere designe-t-il
un tube rigide cintrable sur la Crippa, ou un tuyau souple ?**

La distinction est vitale. Un tuyau souple n'a ni rayon de cintrage, ni
programme, ni geometrie a modeliser : le traiter comme un tube produit un
solide faux, et un plan faux est pire que pas de plan du tout.

Six familles figurent sur le scan :

    Ermeto            tube acier rigide, cintre sur la Crippa
    Pneumatique       tuyau souple
    Lubrification     tuyau souple
    arrosage          tuyau souple
    Forflex           tuyau souple, spirale acier
    Uniflex           tuyau souple, noir

Les codes se lisent en trois triplets : `293 421 008`. Le premier designe la
famille, le troisieme le diametre exterieur en mm. Le deuxieme distingue des
variantes et **ne peut pas etre ignore** : `750 421 012` est un Forflex 20/13
alors que `750 423 012` est un tuyau pneumatique 12/8.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

RIGIDE, SOUPLE = "rigide", "souple"


@dataclass(frozen=True)
class Material:
    """Une reference du catalogue."""

    code: str                   # code BSA normalise, ex. "293 421 008"
    family: str                 # "Ermeto", "Pneumatique", ...
    kind: str                   # RIGIDE ou SOUPLE
    od: float                   # diametre exterieur, mm
    bore: float | None          # diametre interieur, mm
    price_per_m: float | None = None   # indicatif, releve du scan 2026
    note: str = ""

    @property
    def designation(self) -> str:
        if self.bore is None:
            return f"{self.family} Ø{self.od:g}"
        return f"{self.family} {self.od:g}/{self.bore:g}"

    @property
    def wall(self) -> float | None:
        """Epaisseur de paroi. C'est elle qui rend le solide exporte creux."""
        if self.bore is None:
            return None
        return round((self.od - self.bore) / 2.0, 3)

    @property
    def bendable(self) -> bool:
        """Cintrable sur la Crippa : rigide, et dans la plage outillee."""
        return self.kind == RIGIDE and self.od in CRIMPABLE_OD

    @property
    def short(self) -> str:
        return f"{self.od:g}/{self.bore:g}" if self.bore is not None else f"Ø{self.od:g}"


# Diametres pour lesquels BSA possede une matrice de cintrage [DOC 1.2].
# Au-dela, le tube est rigide mais n'est pas cintre chez BSA : le Ø22 est
# explicitement abandonne (« plus cintres chez BSA du a la complexite de mise
# en place de l'outillage »), les Ø25 a Ø38 n'ont jamais eu d'outillage.
CRIMPABLE_OD = {4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 16.0, 18.0}

# ---------------------------------------------------------------- le catalogue
#
# (code, famille, nature, Ø ext, Ø int, prix indicatif au metre)
# Les prix sont releves sur le scan et n'ont qu'une valeur de reperage : ils ne
# sont jamais reportes sur un plan de fabrication.

_CATALOGUE: tuple[tuple[str, str, str, float, float | None, float | None], ...] = (
    # --- Ermeto : tube acier zingue, rigide. La seule famille cintree.
    ("416 421 004", "Ermeto", RIGIDE, 4.0, 2.5, 3.15),
    ("293 421 006", "Ermeto", RIGIDE, 6.0, 4.0, 2.01),
    ("293 421 008", "Ermeto", RIGIDE, 8.0, 6.0, 1.94),
    ("293 421 010", "Ermeto", RIGIDE, 10.0, 8.0, 2.24),
    ("293 422 012", "Ermeto", RIGIDE, 12.0, 9.0, 2.23),
    ("293 421 015", "Ermeto", RIGIDE, 15.0, 12.0, 2.97),
    ("293 421 016", "Ermeto", RIGIDE, 16.0, 13.0, 3.35),
    ("293 421 018", "Ermeto", RIGIDE, 18.0, 14.0, 4.19),
    ("293 421 022", "Ermeto", RIGIDE, 22.0, 17.0, 6.77),
    ("293 422 022", "Ermeto", RIGIDE, 22.0, 19.0, None),
    ("293 421 025", "Ermeto", RIGIDE, 25.0, 20.0, 11.33),
    ("293 421 028", "Ermeto", RIGIDE, 28.0, 22.0, 10.04),
    ("293 421 030", "Ermeto", RIGIDE, 30.0, 24.0, 9.41),
    ("293 421 035", "Ermeto", RIGIDE, 35.0, 27.0, 35.77),
    ("293 421 038", "Ermeto", RIGIDE, 38.0, 30.0, 12.34),

    # --- Pneumatique : tuyau souple.
    ("750 422 004", "Pneumatique", SOUPLE, 4.0, 2.5, 0.31),
    ("750 425 006", "Pneumatique", SOUPLE, 6.0, 4.0, 0.60),
    ("750 423 008", "Pneumatique", SOUPLE, 8.0, 5.5, 0.93),
    ("750 423 010", "Pneumatique", SOUPLE, 10.0, 7.0, 1.51),
    ("750 423 012", "Pneumatique", SOUPLE, 12.0, 8.0, 2.10),
    ("750 421 014", "Pneumatique", SOUPLE, 14.0, 9.5, 3.38),
    ("758 423 016", "Pneumatique", SOUPLE, 16.0, 11.0, 2.30),

    # --- Lubrification : tuyau souple.
    ("751 421 004", "Lubrification", SOUPLE, 4.0, 2.0, 0.69),
    ("758 421 006", "Lubrification", SOUPLE, 6.0, 4.0, 0.47),
    ("751 421 008", "Lubrification", SOUPLE, 8.0, 5.0, 3.75),
    ("751 421 010", "Lubrification", SOUPLE, 10.0, 7.0, 9.80),

    # --- Arrosage : tuyau souple.
    ("778 421 005", "Arrosage", SOUPLE, 10.0, 5.0, 1.21),
    ("778 421 007", "Arrosage", SOUPLE, 14.0, 8.0, 0.97),
    ("778 421 008", "Arrosage", SOUPLE, 14.0, 8.0, 1.10),
    ("778 421 010", "Arrosage", SOUPLE, 16.0, 10.0, 1.66),
    ("778 421 012", "Arrosage", SOUPLE, 19.0, 13.0, 1.38),
    ("778 421 015", "Arrosage", SOUPLE, 21.0, 15.0, 2.04),
    ("778 421 030", "Arrosage", SOUPLE, 42.0, 32.0, 4.84),

    # --- Forflex trans spi acier : tuyau souple a spirale.
    ("750 421 012", "Forflex", SOUPLE, 20.0, 13.0, 12.76),
    ("750 421 018", "Forflex", SOUPLE, 27.0, 20.0, 3.63),
    ("750 421 025", "Forflex", SOUPLE, 33.0, 25.0, 5.72),
    ("750 421 035", "Forflex", SOUPLE, 45.0, 35.0, 10.07),
    ("750 421 040", "Forflex", SOUPLE, 53.0, 40.0, 13.09),
    ("750 421 050", "Forflex", SOUPLE, 65.0, 50.0, 13.31),

    # --- Uniflex noir : tuyau souple.
    ("769 421 035", "Uniflex", SOUPLE, 44.0, 35.0, 16.37),
    ("769 421 050", "Uniflex", SOUPLE, 50.0, 40.0, 15.51),
)

BY_CODE: dict[str, Material] = {
    re.sub(r"\D", "", code): Material(code, fam, kind, od, bore, price)
    for code, fam, kind, od, bore, price in _CATALOGUE
}

# Repli par prefixe, pour un code absent du scan. On ne devine jamais le
# diametre : seule la NATURE est deduite, ce qui suffit a decider si la piece
# entre dans le perimetre Crippa.
FAMILY_BY_PREFIX: dict[str, tuple[str, str]] = {
    "293": ("Ermeto", RIGIDE),
    "416": ("Ermeto", RIGIDE),
    "750": ("Pneumatique ou Forflex", SOUPLE),
    "751": ("Lubrification", SOUPLE),
    "758": ("Pneumatique ou lubrification", SOUPLE),
    "769": ("Uniflex", SOUPLE),
    "778": ("Arrosage", SOUPLE),
}

# Designation commerciale utilisee sur les plans, pour les diametres cintres.
# [DOC 1.2] : tube Ermeto zingue, designation Ø exterieur / Ø interieur.
FINISH = "acier zingué"


def digits(code: object) -> str:
    """'293-421-008', '293 421 008' et 'BSA293421008' donnent le meme resultat."""
    return re.sub(r"\D", "", str(code or ""))


def lookup(code: object) -> Material | None:
    """Retourne la matiere, ou None si le code est vide ou illisible.

    Un code inconnu mais dont le prefixe est reconnu donne quand meme une
    matiere, avec le diametre lu dans le troisieme triplet. C'est suffisant
    pour trancher rigide / souple, qui est la seule decision critique.
    """
    d = digits(code)
    if not d:
        return None
    if d in BY_CODE:
        return BY_CODE[d]

    prefix = d[:3]
    fam_kind = FAMILY_BY_PREFIX.get(prefix)
    if fam_kind is None:
        return None
    family, kind = fam_kind
    od = float(d[-3:]) if len(d) >= 6 and d[-3:].isdigit() else 0.0
    return Material(code=" ".join((d[:3], d[3:6], d[6:])).strip(),
                    family=family, kind=kind, od=od, bore=None,
                    note="code absent du catalogue, famille deduite du préfixe")


def kind_of(code: object) -> str | None:
    """RIGIDE, SOUPLE, ou None si le code n'est pas identifiable."""
    m = lookup(code)
    return None if m is None else m.kind


def is_bendable(code: object) -> bool:
    m = lookup(code)
    return bool(m and m.bendable)


def diameter(code: object) -> int | None:
    """Diametre exterieur entier, pour les seules matieres rigides cintrables.

    On refuse explicitement de rendre un diametre pour un tuyau souple : c'est
    ce refus qui empeche un Uniflex 44/35 d'etre modelise comme un tube.
    """
    m = lookup(code)
    if m is None or not m.bendable:
        return None
    return int(m.od)


def describe(code: object) -> str:
    """Libelle court pour l'interface et les plans."""
    m = lookup(code)
    if m is None:
        return "matière inconnue"
    if m.kind == SOUPLE:
        return f"{m.designation} — tuyau souple, non cintrable"
    if not m.bendable:
        return f"{m.designation} — tube rigide hors outillage Crippa"
    return f"Tube {m.family} {FINISH} {m.short}"


__all__ = ["Material", "RIGIDE", "SOUPLE", "BY_CODE", "CRIMPABLE_OD", "FINISH",
           "lookup", "kind_of", "is_bendable", "diameter", "describe", "digits"]
