"""Lecture d'un fichier LFT, sans hypothese sur sa mise en page.

Le gabarit LFT vient de Wire2000. C'est une liste de fils detournee pour des
tubes : 75 colonnes dont la plupart restent vides. Sa mise en page varie d'un
export a l'autre, et rien ne garantit qu'un tube tienne sur une seule ligne.

Ce module ne suppose donc RIEN :

  * l'en-tete est cherche dans toutes les feuilles et a n'importe quelle ligne,
    par reconnaissance des noms de colonnes et non par position ;
  * les noms de colonnes sont normalises (accents, espaces, casse, tirets) ;
  * les lignes qui parlent du meme tube sont regroupees, quel que soit leur
    ordre et leur nombre ;
  * AUCUNE valeur n'est perdue : chaque colonne conserve la liste ordonnee de
    toutes les valeurs distinctes rencontrees dans le groupe ;
  * les tubes sont ensuite regroupes par lot (colonne LISTE).

Deux notions a ne jamais confondre, et que ce module separe explicitement :

    LISTE      numero du LFT, donc du LOT          ex. 0792-0002-JV
    PROGRAMME  numero du programme, donc du TUBE   ex. 792_JV-412
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------- normalisation

def norm(name: object) -> str:
    """'Recoupe_1 ' -> 'RECOUPE_1'. Insensible aux accents, espaces et tirets."""
    s = str(name or "").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\s\-./]+", "_", s).strip("_")
    return s.upper()


# Valeurs que Wire2000 ecrit pour dire "rien". Elles ne sont pas des donnees.
BLANKS = {"", "@", "-", "NONE", "NULL", "#N/A", "#VALEUR!", "#VALUE!"}

# Colonnes ou 0 signifie reellement "vide" et non "zero mesure".
ZERO_IS_BLANK = {"RATELIER", "NO_MONTAGE", "DENUDER_1", "DENUDER_2", "DIR_1",
                 "DIR_2", "V_DEB_N", "FAISCEAU", "QTE_DEB", "NC_1", "NC_2"}

FALSY_WORDS = {"FAUX", "FALSE", "NON", "NO", "0"}
TRUTHY_WORDS = {"VRAI", "TRUE", "OUI", "YES", "1", "X"}

# Colonnes attendues dans un LFT. Sert a reconnaitre la ligne d'en-tete :
# la ligne qui en contient le plus, et au moins MIN_HEADER_HITS, gagne.
KNOWN_COLUMNS = {
    "REP", "CODE_MAT", "LONGUEUR", "LISTE", "PROGRAMME", "PROGCRIPPA",
    "EMBOUT_1", "EMBOUT_2", "RECOUPE_1", "RECOUPE_2", "OPERATION", "DESSIN",
    "RATELIER", "GAINE", "REMARQUE", "DIMENSION", "DROIT", "GABARIT",
    "FAITMAIN", "VITESSE", "NO_OP", "APPAREIL_1", "APPAREIL_2", "AWG",
    "COULEUR", "ETAT", "QTE_DEB", "NO_CABLE", "TYPE_1", "TYPE_2", "ZONE_1",
    "RETOUCHE_1", "RETOUCHE_2", "NO_RETCH_1", "NO_RETCH_2", "WIRE_NR",
}
MIN_HEADER_HITS = 3
MAX_HEADER_SCAN = 200          # profondeur de recherche de l'en-tete

# Synonymes rencontres selon la version de Wire2000 ou l'outil d'export.
ALIASES = {
    "PROG_CRIPPA": "PROGCRIPPA", "PROGRAMME_CRIPPA": "PROGCRIPPA",
    "ISO": "PROGCRIPPA", "PROGRAMME_ISO": "PROGCRIPPA",
    "REPERE": "REP", "NO_REP": "REP", "N_REP": "REP", "REP_": "REP",
    "CODE_MATIERE": "CODE_MAT", "CODEMAT": "CODE_MAT", "MATIERE": "CODE_MAT",
    "LG": "LONGUEUR", "LONG": "LONGUEUR", "LONGUEUR_TOTALE": "LONGUEUR",
    "LDC": "LISTE", "LFT": "LISTE", "NO_LISTE": "LISTE", "LISTE_LFT": "LISTE",
    "NO_PROGRAMME": "PROGRAMME", "N_PROGRAMME": "PROGRAMME",
    "EMBOUT1": "EMBOUT_1", "EMBOUT2": "EMBOUT_2",
    "RECOUPE1": "RECOUPE_1", "RECOUPE2": "RECOUPE_2",
}


def canonical(name: object) -> str:
    n = norm(name)
    return ALIASES.get(n, n)


def is_blank(v: object, column: str = "") -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip().upper() in BLANKS
    if isinstance(v, (int, float)) and v == 0 and column in ZERO_IS_BLANK:
        return True
    return False


def as_number(v: object) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", ".").replace(" ", "").replace(" ", "")
        m = re.fullmatch(r"[-+]?\d*\.?\d+", s)
        if m:
            return float(s)
    return None


def as_bool(v: object) -> bool | None:
    """'Faux' / 'Vrai' / 'X' -> booleen. None si ce n'est pas un booleen."""
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().upper()
    if s in FALSY_WORDS:
        return False
    if s in TRUTHY_WORDS:
        return True
    return None


# ------------------------------------------------------------------- structures

@dataclass
class SourceRow:
    """Une ligne brute du fichier, telle qu'elle a ete lue."""

    sheet: str
    number: int                                   # numero de ligne Excel, 1-based
    cells: dict[str, object] = field(default_factory=dict)

    def get(self, column: str) -> object:
        return self.cells.get(column)


@dataclass
class Field:
    """Toutes les valeurs vues pour une colonne, dans l'ordre de lecture.

    On garde la liste complete plutot que la premiere valeur : c'est ce qui
    garantit qu'aucune information de l'Excel n'est perdue quand un tube est
    reparti sur plusieurs lignes.
    """

    column: str
    values: list = field(default_factory=list)
    rows: list[int] = field(default_factory=list)

    @property
    def value(self):
        return self.values[0] if self.values else None

    @property
    def conflicting(self) -> bool:
        return len(self.values) > 1

    def __str__(self) -> str:
        return " | ".join(str(v) for v in self.values)


@dataclass
class TubeRecord:
    """Un tube, reconstitue a partir de toutes les lignes qui le concernent."""

    key: str                                       # cle de regroupement
    rep: str = ""
    list_number: str = ""                          # LISTE  = numero de LFT / lot
    program_number: str = ""                       # PROGRAMME = numero de programme
    fields: dict[str, Field] = field(default_factory=dict)
    rows: list[SourceRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- accesseurs
    def get(self, column: str, default=None):
        f = self.fields.get(canonical(column))
        return default if f is None or f.value is None else f.value

    def all(self, column: str) -> list:
        f = self.fields.get(canonical(column))
        return list(f.values) if f else []

    def number(self, column: str) -> float | None:
        for v in self.all(column):
            n = as_number(v)
            if n is not None:
                return n
        return None

    def text(self, column: str, default: str = "") -> str:
        v = self.get(column)
        return default if v is None else str(v).strip()

    def flag(self, column: str) -> bool:
        for v in self.all(column):
            b = as_bool(v)
            if b is not None:
                return b
        return False

    @property
    def conflicts(self) -> dict[str, Field]:
        """Colonnes portant plusieurs valeurs differentes. A afficher, jamais
        a resoudre en silence."""
        return {k: f for k, f in self.fields.items() if f.conflicting}

    # ------------------------------------------------------------ programme ISO
    @property
    def iso(self) -> str:
        """Le programme Crippa, recolle si l'export l'a coupe sur plusieurs lignes."""
        parts = [str(v) for v in self.all("PROGCRIPPA") if str(v).strip()]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        heads = [p for p in parts if p.lstrip().startswith("%")]
        tails = [p for p in parts if not p.lstrip().startswith("%")]
        if len(heads) <= 1:
            # un seul debut de programme : les autres fragments sont sa suite
            return "\n".join(heads + tails)
        # plusieurs programmes complets sur un meme tube : on garde le plus long
        # et on signale, plutot que de fabriquer une geometrie hybride
        self.warnings.append(
            f"{len(heads)} programmes distincts sur le repere {self.rep} : "
            "le plus long est retenu, les autres sont conserves dans les champs")
        return max(heads, key=len)

    @property
    def recut(self) -> float:
        """Recoupe depart + recoupe arrivee. Les deux sont de la matiere en plus
        a debiter, donc elles s'additionnent. [DOC 7.3, reperes 8 et 12]"""
        total = 0.0
        for col in ("RECOUPE_1", "RECOUPE_2"):
            for v in self.all(col):
                n = as_number(v)
                if n:
                    total += n
                    break
        return total

    @property
    def straight(self) -> bool:
        """Tube laisse droit. [DOC 7.3, repere 16]"""
        return self.flag("DROIT")

    @property
    def handmade(self) -> bool:
        return self.flag("FAITMAIN")


@dataclass
class Lot:
    """Un lot = une LFT = une valeur de la colonne LISTE."""

    number: str
    tubes: list[TubeRecord] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.number or "(lot sans numero)"


@dataclass
class Workbook:
    """Le contenu complet d'un fichier LFT, regroupe."""

    path: str = ""
    lots: list[Lot] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)     # ordre d'origine
    sheets_read: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def tubes(self) -> list[TubeRecord]:
        return [t for lot in self.lots for t in lot.tubes]

    def find(self, ref: str) -> TubeRecord | None:
        for t in self.tubes:
            if t.key == ref or t.rep == ref:
                return t
        return None


# ---------------------------------------------------------------- detection

def _header_score(row: tuple) -> int:
    return sum(1 for c in row if canonical(c) in KNOWN_COLUMNS)


def _find_header(rows: list[tuple]) -> int | None:
    """Indice de la ligne d'en-tete, ou None. On prend la meilleure ligne, pas
    la premiere : un export peut porter un titre qui ressemble a un en-tete."""
    best, best_score = None, 0
    for i, row in enumerate(rows[:MAX_HEADER_SCAN]):
        if not row:
            continue
        score = _header_score(row)
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= MIN_HEADER_HITS else None


def _tube_key(cells: dict[str, object]) -> str | None:
    """Cle de regroupement d'une ligne.

    PROGRAMME est la cle la plus sure : il porte deja la liste et le repere
    (792_JV-412). A defaut, LISTE + REP. A defaut, REP seul.
    """
    prog = cells.get("PROGRAMME")
    if not is_blank(prog, "PROGRAMME"):
        return f"P:{str(prog).strip()}"
    rep = cells.get("REP")
    if is_blank(rep, "REP"):
        return None
    rep = str(rep).strip()
    lst = cells.get("LISTE")
    if not is_blank(lst, "LISTE"):
        return f"L:{str(lst).strip()}#{rep}"
    return f"R:{rep}"


def _display_ref(rec: TubeRecord) -> str:
    """Ce qu'on affiche dans la liste des pieces. Le repere s'il est unique."""
    return rec.rep or rec.program_number or rec.key


# ------------------------------------------------------------------- lecture

def read(path: str | Path, sheet: str | None = None) -> Workbook:
    """Lit un fichier LFT et retourne son contenu regroupe par lot puis par tube."""
    from openpyxl import load_workbook

    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"fichier introuvable : {p}")

    wb = load_workbook(p, data_only=True, read_only=True)
    out = Workbook(path=str(p))
    groups: dict[str, TubeRecord] = {}
    order: list[str] = []

    sheets = [wb[sheet]] if sheet else wb.worksheets
    for ws in sheets:
        rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
        h = _find_header(rows)
        if h is None:
            continue
        out.sheets_read.append(ws.title)

        header = [canonical(c) for c in rows[h]]
        # colonnes sans nom : on leur en donne un plutot que de les jeter
        seen: dict[str, int] = {}
        for j, name in enumerate(header):
            if not name:
                header[j] = f"COL_{j + 1}"
            elif name in seen:
                seen[name] += 1
                header[j] = f"{name}__{seen[name]}"
            else:
                seen[name] = 1
        for name in header:
            if name not in out.columns:
                out.columns.append(name)

        for n, raw in enumerate(rows[h + 1:], start=h + 2):
            cells = {}
            for j, name in enumerate(header):
                v = raw[j] if j < len(raw) else None
                if not is_blank(v, name):
                    cells[name] = v
            if not cells:
                continue

            key = _tube_key(cells)
            if key is None:
                # Ligne sans repere ni programme : on la rattache au dernier
                # tube vu, c'est une ligne de continuation.
                if order:
                    key = order[-1]
                else:
                    out.warnings.append(
                        f"{ws.title} ligne {n} : ni REP ni PROGRAMME, ignoree")
                    continue

            rec = groups.get(key)
            if rec is None:
                rec = groups[key] = TubeRecord(key=key)
                order.append(key)
            rec.rows.append(SourceRow(ws.title, n, cells))
            for name, v in cells.items():
                f = rec.fields.get(name)
                if f is None:
                    f = rec.fields[name] = Field(column=name)
                if v not in f.values:            # on garde les valeurs DISTINCTES
                    f.values.append(v)
                    f.rows.append(n)

    wb.close()

    if not out.sheets_read:
        raise ValueError(
            "aucune feuille ne ressemble a un LFT : aucune ligne ne contient "
            f"au moins {MIN_HEADER_HITS} colonnes connues (REP, CODE_MAT, "
            "LONGUEUR, PROGCRIPPA, LISTE, PROGRAMME...)")

    # ------------------------------------------------ finalisation des tubes
    for key in order:
        rec = groups[key]
        rec.rep = rec.text("REP")
        rec.list_number = rec.text("LISTE")
        rec.program_number = rec.text("PROGRAMME")
        if len(rec.rows) > 1:
            rec.warnings.append(
                f"tube reconstitue depuis {len(rec.rows)} lignes "
                f"({', '.join(str(r.number) for r in rec.rows)})")
        for col, f in rec.conflicts.items():
            if col == "PROGCRIPPA":
                continue                          # traite par TubeRecord.iso
            rec.warnings.append(
                f"colonne {col} : {len(f.values)} valeurs differentes ({f})")

    # ------------------------------------------------------ regroupement en lots
    by_lot: dict[str, Lot] = {}
    lot_order: list[str] = []
    for key in order:
        rec = groups[key]
        num = rec.list_number
        lot = by_lot.get(num)
        if lot is None:
            lot = by_lot[num] = Lot(number=num)
            lot_order.append(num)
        lot.tubes.append(rec)
    out.lots = [by_lot[n] for n in lot_order]

    # Tri stable par repere a l'interieur de chaque lot : l'ordre des lignes
    # dans l'Excel ne doit avoir aucune influence sur ce qu'on affiche.
    def rep_order(t: TubeRecord):
        m = re.match(r"^(\d+)", t.rep or "")
        return (0, int(m.group(1)), t.rep) if m else (1, 0, t.rep or t.key)

    for lot in out.lots:
        lot.tubes.sort(key=rep_order)

    # Un repere peut se repeter d'un lot a l'autre : on le signale, l'interface
    # doit alors afficher le lot pour lever l'ambiguite.
    seen_reps: dict[str, str] = {}
    for lot in out.lots:
        for t in lot.tubes:
            if t.rep and t.rep in seen_reps and seen_reps[t.rep] != lot.number:
                out.warnings.append(
                    f"repere {t.rep} present dans les lots {seen_reps[t.rep]} "
                    f"et {lot.number}")
            elif t.rep:
                seen_reps[t.rep] = lot.number
    return out


__all__ = ["read", "Workbook", "Lot", "TubeRecord", "SourceRow", "Field",
           "canonical", "norm", "as_number", "as_bool", "is_blank"]
