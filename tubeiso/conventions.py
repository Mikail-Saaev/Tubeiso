"""Conversion programme ISO -> geometrie, convention BSA.

Sources : la documentation de formation Crippa et les formules de
`archivage_crippa.xlsm`.

Quatre regles, toutes verifiees numeriquement sur le corpus 0792-0002-JV :

1. Un segment droit vaut la SOMME SIGNEE des deplacements Y de son bloc.
   Le programmeur ecrit `Y-35` puis `Y-(L-35)` des que L > 35, exactement la
   formule `=IF(Y>35, Y-35, "")` de la feuille Excel. La somme est signee pour
   absorber l'astuce anti-collision du chapitre 5.8, ou le programme avance de
   30, tourne de 180 puis revient de 30.

2. Le premier segment est R12. Le dernier n'est PAS ecrit : il se deduit de
   R6 via la formule d'allongement. DS n'est qu'un commentaire d'aide, et
   l'expert BSA a explicitement dit de ne pas s'en servir comme reference.

3. Les Y sont des longueurs tangente-a-tangente, arrondies a 0.5 mm.
   Confirme par la mesure Catia du tube 410 : 33.861 -> 34, 63.591 -> 63.5.

4. R15 n'est PAS l'angle du tube. Il porte le supplement de retour elastique
   [DOC 5.4], et TOUTE la chaine de longueur travaille sur l'angle reel.

   C'est contre-intuitif, alors voici pourquoi. Le [DOC 8.3.4] dit de reporter
   dans la colonne R15 du classeur Archivage l'angle MESURE dans Catia. Le
   supplement d'elasticite n'est ajoute qu'au moment d'ecrire le programme.
   La colonne R15 du classeur et le R15 du programme ne contiennent donc pas
   la meme chose, et c'est le classeur — donc l'angle reel — qui a produit le
   R6 grave dans le programme.

   Le corpus le confirme. En deduisant le dernier segment avec les angles
   reels, l'ecart au DS tombe a 0.41 mm en moyenne quadratique ; avec R15
   brut il vaut 1.22 mm et depasse 2 mm sur le repere 411.

Le choix du mode de correction est expose dans les reglages :

    entier          on inverse l'arrondi du programmeur (defaut, angles ronds)
    proportionnel   r15 * 90 / R15_a_90, sans arrondi
    brut            aucune correction, comportement des versions <= v4
"""
from __future__ import annotations

from . import bsa
from .model import Bend, Tooling, TubeProgram
from .parsers.crippa import RawProgram


class BSAConvention:
    name = "bsa"
    description = ("somme signee des Y, dernier segment deduit de R6, "
                   "angle reel apres retour elastique [DOC+XLSM]")

    def build(self, raw: RawProgram, tooling: Tooling, recut: float = 0.0,
              angle_mode: str = bsa.DEFAULT_ANGLE_MODE,
              use_true_angles: bool | None = None) -> TubeProgram:
        # compatibilite avec l'ancien parametre booleen
        if use_true_angles is not None:
            angle_mode = bsa.DEFAULT_ANGLE_MODE if use_true_angles else "brut"
        if angle_mode not in bsa.ANGLE_MODES:
            angle_mode = bsa.DEFAULT_ANGLE_MODE

        warnings = list(raw.warnings)
        diameter = raw.diameter or tooling.diameter
        r15 = list(raw.angles)

        clr = tooling.clr
        if clr is None and int(diameter or 0) in bsa.RM:
            clr = bsa.bend_radius(diameter)

        head = raw.init.get("R12")
        if head is None:
            warnings.append("R12 absent : premier segment inconnu")
            head = 0.0
        mid = [b.segment() for b in raw.blocks[:-1]] if raw.blocks else []
        straights = [head, *mid]

        # --- angles reels : ils servent a la fois a la geometrie et au calcul
        # de longueur, parce que c'est eux que porte la feuille Archivage.
        real = [bsa.real_angle(a, diameter, angle_mode) for a in r15]
        angles = [a for a, _ in real]

        # --- dernier segment : deduit de R6 par la formule d'allongement
        last = None
        if raw.declared_length is not None and clr is not None and raw.complete:
            try:
                last = bsa.last_straight(
                    raw.declared_length, straights, angles, diameter, recut,
                    rm=clr, elongation=tooling.elongation or None)
            except KeyError as exc:
                warnings.append(str(exc))
        if last is None:
            last = raw.ds if raw.ds is not None else 0.0
            if raw.complete:
                warnings.append("dernier segment repris de DS, valeur non fiable")
        elif last < 0:
            warnings.append(
                f"dernier segment négatif ({last:.1f} mm) : programme incomplet "
                "ou R6 faux")
        straights.append(last)

        bends = []
        for i, (true, delta) in enumerate(real):
            rot = raw.blocks[i - 1].rotation() if i > 0 else 0.0
            verrou = (angle_mode != "brut"
                      and bsa.locked_angle(r15[i]) == true)
            bends.append(Bend(angle=true, rotation=rot, clr=clr,
                              r15=r15[i], springback=delta, locked=verrou))

        # --- faux pli a 0 degre [DOC 5.3 ; XLSM Feuil2!B53]
        # « Le dernier segment ne doit pas faire plus de 450 mm sinon faire un
        # faux pli a 0 deg. » Ce bloc ne plie rien : le tube reste droit de part
        # et d'autre. Le conserver couperait un segment reel en deux, fausserait
        # le controle DS et ferait croire a un coude au sous-traitant.
        straights, bends, merged = _merge_zero_bends(straights, bends)
        for length in merged:
            warnings.append(
                f"faux pli à 0° fusionné : segment droit réel de {length:.1f} mm "
                "[DOC 5.3]")

        if angle_mode != "brut" and any(b.springback for b in bends):
            total = sum(b.springback for b in bends)
            warnings.append(
                f"retour élastique retiré : {total:g}° au total sur "
                f"{sum(1 for b in bends if b.springback)} coude(s) [DOC 5.4]")

        verrous = [b for b in bends if b.locked]
        if verrous:
            ecrits = sorted({f"{b.r15:g}" for b in verrous})
            warnings.append(
                f"angle verrouillé à 90° sur {len(verrous)} coude(s) "
                f"(R15 écrit : {', '.join(ecrits)}) : la règle d'atelier prime "
                "sur le coefficient d'élasticité [bsa.ANGLE_LOCK]")

        return TubeProgram(
            ref=raw.name, program=raw.name, diameter=diameter,
            tooling=raw.tooling or tooling.name,
            declared_length=raw.declared_length, ds=raw.ds, comment=raw.comment,
            straights=straights, bends=bends, params=dict(raw.init),
            source=raw.source, complete=raw.complete, angle_mode=angle_mode,
            r7_released=any(b.params.get("R7") == 0 for b in raw.blocks),
            false_bends=merged,
            warnings=warnings,
        )

    def build_straight(self, ref: str, length: float, diameter: float,
                       tooling: Tooling) -> TubeProgram:
        """Tube laisse droit : pas de programme, juste une longueur et un Ø.

        « Meme s'il n'y a pas de programme, il faut generer la 3D avec
        uniquement la longueur et le diametre. » — consigne BSA.
        """
        return TubeProgram(
            ref=ref, program="", diameter=diameter or tooling.diameter,
            tooling=tooling.name, declared_length=length,
            comment="tube droit, sans programme de cintrage",
            straights=[float(length)], bends=[], complete=True, straight=True,
        )


def _merge_zero_bends(straights, bends, tol: float = 1e-9):
    """Supprime les coudes d'angle nul en recollant leurs segments voisins.

    Retourne (straights, bends, longueurs des segments recolles).
    """
    out_s = list(straights)
    out_b = list(bends)
    merged: list[float] = []
    i = 0
    while i < len(out_b):
        if abs(out_b[i].angle) <= tol and i + 1 < len(out_s):
            out_s[i] = out_s[i] + out_s[i + 1]
            merged.append(out_s[i])
            del out_s[i + 1]
            del out_b[i]
            continue
        i += 1
    return out_s, out_b, merged


REGISTRY = {BSAConvention.name: BSAConvention()}
DEFAULT = BSAConvention.name

# Noms utilises par les versions precedentes, avant que la documentation BSA
# et le fichier Archivage ne permettent de trancher. Ils pointent tous sur la
# convention confirmee plutot que de faire echouer une ancienne configuration.
LEGACY = {"feed_only", "feed_plus_retract", "vertex_feed", "vertex_total"}


def get(name: str = DEFAULT):
    if name in REGISTRY:
        return REGISTRY[name]
    if name in LEGACY or not name:
        return REGISTRY[DEFAULT]
    raise KeyError(f"convention '{name}' inconnue. Disponibles : "
                   f"{sorted(REGISTRY)}")
