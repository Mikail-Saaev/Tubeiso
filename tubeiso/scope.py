"""Perimetre de traitement : quelles pieces l'application a le droit de sortir.

Regle de base, posee par BSA : **on ne traite que les tuyaux equipes d'une
PROGCRIPPA.** Tout le reste est ecarte, parce qu'une piece sans programme n'a
pas de geometrie et qu'un modele invente est plus dangereux qu'une absence de
modele.

Un tube RIGIDE laisse droit fait exception, et c'est la consigne BSA : « meme
s'il n'y a pas de programme, il faut generer la 3D avec uniquement la longueur
et le diametre. » Il n'a pas de geometrie a deviner — une droite et un
diametre suffisent — donc il est traite, et son plan porte la mention.

Ecarter n'est pas oublier. Chaque piece hors perimetre ressort avec son motif,
et c'est ce qui permet de rendre compte de 100 % des lignes d'une LFT :

    traitee            programme complet : plan cote + 3D
    tube droit         pas de programme, mais matiere et longueur connues
    exclue             motif explicite, aucun fichier genere

Les motifs sont stables et destines a etre comptes : ils servent de colonne
dans l'index et de cle de regroupement dans le rapport de campagne.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import bsa, materials

TRAITE, DROIT, EXCLU = "traitée", "tube droit", "exclue"

# Motifs d'exclusion, du plus frequent au plus rare.
SANS_PROGRAMME = "sans_programme"
TUBE_DROIT = "tube_droit_sans_programme"
MATIERE_SOUPLE = "matière_souple"
MATIERE_INCONNUE = "matière_inconnue"
HORS_OUTILLAGE = "hors_outillage_crippa"
PROGRAMME_TRONQUE = "programme_tronqué"
SANS_CINTRAGE = "programme_sans_cintrage"
DIAMETRE_INCONNU = "diamètre_introuvable"
FAIT_MAIN = "plié_à_la_main"
LONGUEUR_ABSENTE = "longueur_absente"

LIBELLES = {
    SANS_PROGRAMME: "aucune PROGCRIPPA : pas de géométrie exploitable",
    TUBE_DROIT: "tube droit : ni coudes, ni programme — longueur et Ø suffisent",
    MATIERE_SOUPLE: "tuyau souple : ni cintré, ni modélisable sur la Crippa",
    MATIERE_INCONNUE: "code matière absent ou non reconnu",
    HORS_OUTILLAGE: "diamètre rigide mais sans outillage de cintrage BSA",
    PROGRAMME_TRONQUE: "programme incomplet (M30 absent) : géométrie fausse",
    SANS_CINTRAGE: "programme présent mais aucun bloc de cintrage",
    DIAMETRE_INCONNU: "diamètre introuvable dans le programme et dans CODE_MAT",
    FAIT_MAIN: "tube coché FAITMAIN : façonné hors Crippa",
    LONGUEUR_ABSENTE: "tube droit sans longueur : rien à modéliser",
}


@dataclass
class Verdict:
    """Decision de perimetre pour une piece."""

    status: str                       # TRAITE ou EXCLU
    reason: str = ""                  # code stable, vide si traitee
    detail: str = ""                  # phrase lisible
    material: materials.Material | None = None
    diameter: int | None = None
    notes: list[str] = None           # remarques non bloquantes

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []

    @property
    def ok(self) -> bool:
        """Vrai des que la piece produit des fichiers, cintree ou droite."""
        return self.status in (TRAITE, DROIT)

    @property
    def straight(self) -> bool:
        return self.status == DROIT

    @property
    def kind(self) -> str:
        """rigide / souple / inconnue — ce que l'interface affiche en pastille."""
        return self.material.kind if self.material else "inconnue"

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
            "paroi": m.wall if m else None,
            "remarques": list(self.notes),
        }


def has_program(text: object) -> bool:
    """Un vrai programme, pas une cellule qui contient trois espaces."""
    s = str(text or "").strip()
    if not s:
        return False
    upper = s.upper()
    return "%MPF" in upper or ("L2" in upper and "R15" in upper)


def evaluate(record, raw=None) -> Verdict:
    """Decide du sort d'une piece.

    `record` est un `lft.TubeRecord`. `raw` est le `RawProgram` deja parse,
    quand il l'a ete ; le laisser a None economise un parsing lorsque la piece
    est de toute facon hors perimetre.
    """
    code = record.get("CODE_MAT")
    mat = materials.lookup(code)
    notes: list[str] = []

    # --- 1. La matiere d'abord : elle tranche sans rien lire du programme.
    if mat is not None and mat.kind == materials.SOUPLE:
        return Verdict(EXCLU, MATIERE_SOUPLE,
                       f"{mat.designation} ({mat.family}) — {LIBELLES[MATIERE_SOUPLE]}",
                       mat)

    # --- 2. Le programme, ou son absence.
    iso = record.iso if hasattr(record, "iso") else ""
    if not has_program(iso):
        if record.handmade:
            return Verdict(EXCLU, FAIT_MAIN, LIBELLES[FAIT_MAIN], mat)
        # Tube rigide sans programme : une droite suffit a le modeliser, a
        # condition de connaitre son diametre et sa longueur. C'est la consigne
        # BSA, et c'est ce qui permet d'exporter tout le parc rigide.
        d = materials.diameter(code)
        if mat is not None and mat.kind == materials.RIGIDE and not mat.bendable:
            return Verdict(EXCLU, HORS_OUTILLAGE,
                           f"{mat.designation} — {LIBELLES[HORS_OUTILLAGE]}", mat)
        if d is None:
            return Verdict(EXCLU, MATIERE_INCONNUE if mat is None else DIAMETRE_INCONNU,
                           LIBELLES[MATIERE_INCONNUE if mat is None else DIAMETRE_INCONNU],
                           mat)
        longueur = record.number("LONGUEUR") if hasattr(record, "number") else None
        if not longueur or float(longueur) <= 0:
            return Verdict(EXCLU, LONGUEUR_ABSENTE, LIBELLES[LONGUEUR_ABSENTE], mat, d)
        notes = [] if record.straight else [
            "aucune PROGCRIPPA et case DROIT non cochée : traité comme tube droit"]
        return Verdict(DROIT, TUBE_DROIT, LIBELLES[TUBE_DROIT], mat, d, notes)

    if record.handmade:
        notes.append("coché FAITMAIN alors qu'un programme Crippa est présent")

    # --- 3. Matiere non identifiee : on accepte si le programme porte un Ø.
    diam = materials.diameter(code)
    if mat is None:
        notes.append("code matière non reconnu, diamètre repris du programme")
    elif mat.kind == materials.RIGIDE and not mat.bendable:
        return Verdict(EXCLU, HORS_OUTILLAGE,
                       f"{mat.designation} — {LIBELLES[HORS_OUTILLAGE]}", mat)

    # --- 4. Le programme doit etre complet et porter au moins un coude.
    if raw is not None:
        if raw.diameter:
            d = int(raw.diameter)
            if diam and d != diam:
                notes.append(
                    f"le programme annonce Ø{d} et CODE_MAT Ø{diam} : "
                    "le programme fait foi")
            diam = d
        if not raw.complete:
            return Verdict(EXCLU, PROGRAMME_TRONQUE, LIBELLES[PROGRAMME_TRONQUE],
                           mat, diam, notes)
        if not raw.blocks:
            return Verdict(EXCLU, SANS_CINTRAGE, LIBELLES[SANS_CINTRAGE],
                           mat, diam, notes)

    if diam is None:
        return Verdict(EXCLU, MATIERE_INCONNUE if mat is None else DIAMETRE_INCONNU,
                       LIBELLES[MATIERE_INCONNUE if mat is None else DIAMETRE_INCONNU],
                       mat, None, notes)
    if diam not in bsa.RM:
        return Verdict(EXCLU, HORS_OUTILLAGE,
                       f"Ø{diam} — {LIBELLES[HORS_OUTILLAGE]}", mat, diam, notes)

    return Verdict(TRAITE, "", "", mat, diam, notes)


def summarise(verdicts) -> dict:
    """Compte les pieces par statut puis par motif. Sert au rapport de campagne."""
    out = {"total": 0, TRAITE: 0, DROIT: 0, EXCLU: 0, "motifs": {}}
    for v in verdicts:
        out["total"] += 1
        out[v.status] = out.get(v.status, 0) + 1
        if v.reason:
            out["motifs"][v.reason] = out["motifs"].get(v.reason, 0) + 1
    return out


__all__ = ["Verdict", "evaluate", "summarise", "has_program", "LIBELLES",
           "TRAITE", "DROIT", "EXCLU", "LONGUEUR_ABSENTE",
           "SANS_PROGRAMME", "TUBE_DROIT", "MATIERE_SOUPLE",
           "MATIERE_INCONNUE", "HORS_OUTILLAGE", "PROGRAMME_TRONQUE",
           "SANS_CINTRAGE", "DIAMETRE_INCONNU", "FAIT_MAIN"]
