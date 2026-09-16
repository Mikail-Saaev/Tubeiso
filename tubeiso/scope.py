"""Perimetre de traitement : ce que l'application a le droit de sortir.

**Une piece n'est ecartee que lorsqu'il n'y a rien a mettre sur le papier.**
C'est la seule regle. Tout le reste est traite, avec le livrable que ses
donnees permettent :

    traitée      programme Crippa exploitable   plan cote + modele 3D
    tube droit   pas de coude, mais Ø et longueur    plan de debit + 3D
    débit seul   forme non definie (souple, façonne a la main)   fiche de debit
    exclue       ni longueur, ni matiere identifiable   rien

La version precedente ecartait bien davantage, et a tort. Trois exemples :

* un Ermeto Ø28 coupe droit etait rejete parce que BSA n'a pas de matrice de
  cintrage a ce diametre — alors qu'on ne le plie pas. Il se modelise en un
  cylindre creux, verifie etanche ;
* un programme tronque etait rejete en bloc, alors que la troncature ne mange
  pas toujours de la geometrie. Il est desormais traite, et c'est le controle
  de longueur qui tranche : si le developpe recalcule colle au R6, la
  troncature n'a coute que la fin de ligne ; sinon le plan porte le bandeau
  ERREUR et aucun modele 3D n'est ecrit ;
* un tuyau souple ne produisait rien du tout, alors que sa matiere, sa
  longueur et sa quantite sont utiles a l'approvisionnement. Il sort en fiche
  de debit, marquee « forme non definie », qui ne peut pas passer pour un plan.

Les motifs sont stables et destines a etre comptes : ils servent de colonne
dans l'index et de cle de regroupement dans le rapport de campagne.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import bsa, materials

TRAITE, DROIT, DEBIT, EXCLU = "traitée", "tube droit", "débit seul", "exclue"

# --- motifs. Les trois premiers menent a un livrable, les derniers a rien.
TUBE_DROIT = "tube_droit_sans_programme"
SANS_CINTRAGE = "programme_sans_cintrage"
HORS_OUTILLAGE = "hors_outillage_crippa"
MATIERE_SOUPLE = "matière_souple"
FAIT_MAIN = "plié_à_la_main"
PROGRAMME_TRONQUE = "programme_tronqué"

MATIERE_INCONNUE = "matière_inconnue"
DIAMETRE_INCONNU = "diamètre_introuvable"
LONGUEUR_ABSENTE = "longueur_absente"

LIBELLES = {
    TUBE_DROIT: "tube droit : ni coudes, ni programme — longueur et Ø suffisent",
    SANS_CINTRAGE: "programme sans bloc de cintrage : la pièce reste droite",
    HORS_OUTILLAGE: "pas de matrice de cintrage BSA à ce diamètre",
    MATIERE_SOUPLE: "tuyau souple : forme non définie, débit seul",
    FAIT_MAIN: "façonné à la main : forme non définie, débit seul",
    PROGRAMME_TRONQUE: "programme incomplet (M30 absent) : contrôle de longueur renforcé",
    MATIERE_INCONNUE: "code matière absent ou non reconnu",
    DIAMETRE_INCONNU: "diamètre introuvable dans le programme et dans CODE_MAT",
    LONGUEUR_ABSENTE: "ni longueur ni programme : rien à produire",
}

# Statuts qui produisent des fichiers.
LIVRABLES = (TRAITE, DROIT, DEBIT)


@dataclass
class Verdict:
    """Decision de perimetre pour une piece."""

    status: str
    reason: str = ""                  # code stable, vide si rien a signaler
    detail: str = ""                  # phrase lisible
    material: materials.Material | None = None
    diameter: float | None = None     # diametre exterieur retenu
    notes: list[str] = None           # remarques non bloquantes
    length: float | None = None       # longueur retenue pour le debit

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []

    @property
    def ok(self) -> bool:
        """Vrai des que la piece produit au moins un fichier."""
        return self.status in LIVRABLES

    @property
    def straight(self) -> bool:
        return self.status == DROIT

    @property
    def cut_only(self) -> bool:
        """Pas de forme : ni plan de cintrage, ni modele 3D."""
        return self.status == DEBIT

    @property
    def modelled(self) -> bool:
        """La piece a une geometrie : plan cote et solide."""
        return self.status in (TRAITE, DROIT)

    @property
    def kind(self) -> str:
        """rigide / souple / inconnue — ce que l'interface affiche en pastille."""
        return self.material.kind if self.material else "inconnue"

    @property
    def wall(self) -> float | None:
        return self.material.wall if self.material else None

    def as_dict(self) -> dict:
        m = self.material
        return {
            "statut": self.status,
            "motif": self.reason,
            "detail": self.detail,
            "nature": self.kind,
            "matiere": m.designation if m else None,
            "code_matiere": m.code if m else None,
            "famille": m.family if m else None,
            "cintrable": bool(m and m.bendable),
            "diametre": self.diameter,
            "paroi": self.wall,
            "longueur": self.length,
            "livrable": {TRAITE: "plan coté + modèle 3D",
                         DROIT: "plan de débit + modèle 3D",
                         DEBIT: "fiche de débit",
                         EXCLU: "aucun"}[self.status],
            "remarques": list(self.notes),
        }


def has_program(text: object) -> bool:
    """Un vrai programme, pas une cellule qui contient trois espaces."""
    s = str(text or "").strip()
    if not s:
        return False
    upper = s.upper()
    return "%MPF" in upper or ("L2" in upper and "R15" in upper)


def _length(record) -> float | None:
    if not hasattr(record, "number"):
        return None
    v = record.number("LONGUEUR")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def evaluate(record, raw=None) -> Verdict:
    """Decide du sort d'une piece.

    `record` est un `lft.TubeRecord`. `raw` est le `RawProgram` deja parse,
    quand il l'a ete.
    """
    code = record.get("CODE_MAT")
    mat = materials.lookup(code)
    od = materials.outer_diameter(code)
    longueur = _length(record)
    notes: list[str] = []

    iso = record.iso if hasattr(record, "iso") else ""
    programme = has_program(iso)

    # ------------------------------------------------------------ 1. programme
    if programme:
        if longueur is None and raw is not None and raw.declared_length:
            # Une LFT peut ne pas porter LONGUEUR : R6 la remplace pour le debit.
            longueur = float(raw.declared_length)
        if record.handmade:
            notes.append("coché FAITMAIN alors qu'une PROGCRIPPA est présente : "
                         "le programme fait foi")
        diam = od
        if raw is not None and raw.diameter:
            d = float(raw.diameter)
            if diam and abs(diam - d) > 0.01:
                notes.append(f"le programme annonce Ø{d:g} et CODE_MAT Ø{diam:g} : "
                             "le programme fait foi")
            diam = d
        if mat is not None and mat.kind == materials.SOUPLE:
            notes.append(f"CODE_MAT annonce un tuyau souple ({mat.designation}) "
                         "alors qu'un programme de cintrage existe : code à vérifier")

        if diam is None:
            return _no_geometry(record, mat, None, longueur, DIAMETRE_INCONNU, notes)

        if raw is not None and not raw.blocks:
            # Un programme sans bloc de cintrage decrit une piece droite.
            if longueur or (raw.declared_length or 0) > 0:
                notes.append(LIBELLES[SANS_CINTRAGE])
                return Verdict(DROIT, SANS_CINTRAGE, LIBELLES[SANS_CINTRAGE],
                               mat, diam, notes, longueur)
            return Verdict(EXCLU, LONGUEUR_ABSENTE, LIBELLES[LONGUEUR_ABSENTE],
                           mat, diam, notes)

        if int(diam) not in bsa.RM:
            # Des coudes, mais aucune matrice connue a ce diametre : le rayon
            # de cintrage est inconnu, donc la forme aussi. Reste le debit.
            notes.append(f"Ø{diam:g} absent des tables de cintrage BSA")
            return _no_geometry(record, mat, diam, longueur, HORS_OUTILLAGE, notes)

        if raw is not None and not raw.complete:
            notes.append("programme tronqué (M30 absent) : la géométrie est "
                         "reconstruite, le contrôle de longueur tranche")
            return Verdict(TRAITE, PROGRAMME_TRONQUE, LIBELLES[PROGRAMME_TRONQUE],
                           mat, diam, notes, longueur)
        return Verdict(TRAITE, "", "", mat, diam, notes, longueur)

    # -------------------------------------------------------- 2. sans programme
    if mat is not None and mat.kind == materials.SOUPLE:
        return _no_geometry(record, mat, od, longueur, MATIERE_SOUPLE, notes)

    if record.handmade:
        return _no_geometry(record, mat, od, longueur, FAIT_MAIN, notes)

    if od is None:
        return Verdict(EXCLU,
                       MATIERE_INCONNUE if mat is None else DIAMETRE_INCONNU,
                       LIBELLES[MATIERE_INCONNUE if mat is None else DIAMETRE_INCONNU],
                       mat, None, notes)
    if longueur is None:
        return Verdict(EXCLU, LONGUEUR_ABSENTE, LIBELLES[LONGUEUR_ABSENTE],
                       mat, od, notes)

    if not record.straight:
        notes.append("aucune PROGCRIPPA et case DROIT non cochée : "
                     "traité comme tube droit")
    return Verdict(DROIT, TUBE_DROIT, LIBELLES[TUBE_DROIT], mat, od, notes,
                   longueur)


def _no_geometry(record, mat, diam, longueur, reason, notes) -> Verdict:
    """Forme non definie : fiche de debit si la longueur existe, sinon rien."""
    detail = LIBELLES[reason]
    if mat is not None:
        detail = f"{mat.designation} — {detail}"
    if longueur is None:
        return Verdict(EXCLU, LONGUEUR_ABSENTE,
                       f"{detail} · {LIBELLES[LONGUEUR_ABSENTE]}",
                       mat, diam, notes)
    return Verdict(DEBIT, reason, detail, mat, diam, notes, longueur)


def tooling_for(cfg, verdict: Verdict, record=None):
    """Outillage a utiliser pour cette piece, paroi comprise.

    La table `tooling.json` ne couvre que les diametres cintres. Pour un tube
    droit hors de cette plage, le catalogue matiere fournit quand meme la paroi
    — sans elle le solide exporte serait plein au lieu d'etre creux.
    """
    code = record.get("CODE_MAT") if record is not None else None
    t = cfg.for_program("", verdict.diameter, code)
    if verdict.diameter and not t.diameter:
        t = replace(t, diameter=float(verdict.diameter))
    if t.wall is None and verdict.wall:
        t = replace(t, wall=verdict.wall)
    if not t.material and verdict.material:
        t = replace(t, material=materials.describe(verdict.material.code))
    return t


def summarise(verdicts) -> dict:
    """Compte les pieces par statut puis par motif."""
    out = {"total": 0, TRAITE: 0, DROIT: 0, DEBIT: 0, EXCLU: 0, "motifs": {}}
    for v in verdicts:
        out["total"] += 1
        out[v.status] = out.get(v.status, 0) + 1
        if v.reason:
            out["motifs"][v.reason] = out["motifs"].get(v.reason, 0) + 1
    return out


__all__ = ["Verdict", "evaluate", "summarise", "has_program", "tooling_for",
           "LIBELLES", "LIVRABLES", "TRAITE", "DROIT", "DEBIT", "EXCLU",
           "TUBE_DROIT", "SANS_CINTRAGE", "HORS_OUTILLAGE", "MATIERE_SOUPLE",
           "FAIT_MAIN", "PROGRAMME_TRONQUE",
           "MATIERE_INCONNUE", "DIAMETRE_INCONNU", "LONGUEUR_ABSENTE"]
