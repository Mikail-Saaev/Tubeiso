"""Parseur du dialecte ISO Crippa / SINUMERIK 840D.

Ce module ne fait QUE de la lecture syntaxique. Il ne decide rien sur le sens
des longueurs : il expose les mouvements bruts et laisse conventions.py les
interpreter. C'est volontaire, parce que l'interpretation est l'inconnue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import bsa

RE_HEADER = re.compile(r"^%\s*MPF\s*(\S+)", re.I)
# Commentaire : on capture jusqu'a la DERNIERE parenthese de la ligne. Le
# commentaire d'en-tete contient lui-meme des parentheses imbriquees
# (« MASTERCUT 1.7/2.1/1.65 (machine), Ø6 (diametre en mm) ») : s'arreter a la
# premiere fermante perdait le diametre, la longueur et le DS.
RE_COMMENT = re.compile(r"\((.*)\)\s*$")
# Appel d'outillage : tolere un commentaire a la suite, comme « L56 (tete du bas) ».
RE_SUBCALL = re.compile(r"^L(\d+)\s*(?:\(.*)?$", re.I)
RE_BLOCK = re.compile(r"^N(\d+)\s+L(\d+)\b(.*)$", re.I)
RE_RPARAM = re.compile(r"\bR(\d+)\s*=\s*(-?\d+(?:\.\d+)?)")
RE_AXIS = re.compile(r"\b([YBCXZ])\s*(-?\d+(?:\.\d+)?)")
RE_DIAM = re.compile(r"[ØO�]\s*(\d+(?:\.\d+)?)", re.I)
RE_LEN = re.compile(r"\bL\s*=\s*(\d+(?:\.\d+)?)", re.I)
RE_DS = re.compile(r"\bDS\s*=?\s*(\d+(?:\.\d+)?)", re.I)
RE_RECOUPE = re.compile(r"\bRECOUPE\s*=?\s*(\d+(?:\.\d+)?)", re.I)

TRUNCATION_LIMIT = 255  # champ Text(255) : la troncature silencieuse historique


@dataclass
class Move:
    """Un deplacement rapide entre deux blocs de cintrage."""

    incremental: bool = True
    axes: dict[str, float] = field(default_factory=dict)

    def y(self) -> float:
        """Deplacement Y signe. Le signe compte : l'astuce anti-collision du
        chapitre 5.8 avance puis revient en arriere."""
        return self.axes.get("Y", 0.0)

    def b(self) -> float | None:
        return self.axes.get("B")


@dataclass
class RawBlock:
    """Un bloc N<i> : un coude, suivi des mouvements qui le separent du suivant."""

    index: int
    sub: int                                  # 2 = coude intermediaire, 3 = coude final
    params: dict[str, float] = field(default_factory=dict)
    moves: list[Move] = field(default_factory=list)

    @property
    def angle(self) -> float | None:
        return self.params.get("R15")

    def segment(self) -> float:
        """Longueur du segment droit suivant ce coude.

        Somme SIGNEE des Y du bloc. Le programmeur ecrit `Y-35` puis
        `Y-(L-35)` des que L depasse 35 : les deux s'additionnent.
        [DOC 4.2 ; XLSM Feuil1!E20]
        """
        return abs(sum(m.y() for m in self.moves))

    def rotation(self) -> float:
        for m in self.moves:
            if m.b() is not None:
                return m.b()
        return 0.0


@dataclass
class RawProgram:
    """Sortie brute du parseur, avant interpretation des longueurs."""

    name: str = ""
    comment: str = ""
    machine: str = ""                  # ex. "MASTERCUT 1.7/2.1/1.65"
    tooling: str = ""                  # ex. "L56"
    head: int | None = None            # 4 = tete du haut, 5 = tete du bas
    head_changes: list[str] = field(default_factory=list)
    diameter: float | None = None
    declared_length: float | None = None
    ds: float | None = None
    recut: float | None = None
    loading: str = ""                  # L1 ou L4
    init: dict[str, float] = field(default_factory=dict)
    blocks: list[RawBlock] = field(default_factory=list)
    complete: bool = False
    source: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def angles(self) -> list[float]:
        return [b.angle for b in self.blocks if b.angle is not None]


def parse(text: str, ref: str = "") -> RawProgram:
    """Lit un programme MPF et retourne sa structure brute."""
    prog = RawProgram(source=text)
    if not text or not text.strip():
        prog.warnings.append("programme vide")
        return prog

    # Detection de troncature AVANT toute analyse : un programme coupe donne
    # des resultats geometriques faux mais plausibles, ce qui est pire qu'une erreur.
    if len(text) == TRUNCATION_LIMIT:
        prog.warnings.append(
            f"longueur exactement {TRUNCATION_LIMIT} caracteres : "
            "troncature Text(255) tres probable"
        )

    current: RawBlock | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        m = RE_HEADER.match(line)
        if m:
            prog.name = m.group(1)
            continue

        if line.lstrip().startswith("("):
            m = RE_COMMENT.search(line)
            body = m.group(1).strip() if m else line.lstrip()[1:].strip()
            prog.comment = body if not prog.comment else prog.comment
            _parse_comment(prog, body)
            continue
        # Certains exports perdent les parentheses. Une ligne qui ne contient
        # ni bloc, ni appel, ni axe, mais un Ø ou un L=, reste un commentaire.
        if (not RE_BLOCK.match(line) and not RE_SUBCALL.match(line)
                and (RE_DIAM.search(line) or RE_LEN.search(line)
                     or RE_DS.search(line))
                and not line.upper().startswith(("L1 ", "G9", "G0", "N"))):
            prog.comment = prog.comment or line
            _parse_comment(prog, line)
            continue

        if "M30" in line:
            prog.complete = True
            continue

        m = RE_SUBCALL.match(line)
        if m:
            n = int(m.group(1))
            if n in (1, 4):                 # L1 / L4 = cycles de chargement
                prog.loading = f"L{n}"
                continue
            if n <= 3:                      # L2 / L3 sans bloc N : ignore
                continue
            code = f"L{n}"                  # L54, L412... = outillage
            try:
                head, dia = bsa.parse_tooling(code)
            except ValueError as exc:
                prog.warnings.append(str(exc))
                continue
            if prog.tooling:                # [DOC 5.6] changement de tete
                prog.head_changes.append(code)
                prog.warnings.append(
                    f"changement de tete en cours de programme ({prog.tooling}"
                    f" -> {code})")
            else:
                prog.tooling, prog.head = code, head
                if prog.diameter is None:
                    prog.diameter = float(dia)
            continue

        m = RE_BLOCK.match(line)
        if m:
            current = RawBlock(
                index=int(m.group(1)),
                sub=int(m.group(2)),
                params=_rparams(m.group(3)),
            )
            prog.blocks.append(current)
            continue

        if line.upper().startswith("L1 ") or line.upper() == "L1":
            prog.init = _rparams(line)
            continue

        axes = dict(RE_AXIS.findall(line))
        if axes:
            mv = Move(
                incremental="G91" in line.upper(),
                axes={k.upper(): float(v) for k, v in axes.items()},
            )
            if current is not None:
                current.moves.append(mv)
            continue

    if not prog.complete:
        prog.warnings.append("pas de M30 : programme incomplet, geometrie inexploitable")
    if not prog.blocks:
        prog.warnings.append("aucun bloc de cintrage trouve")
    if prog.blocks and prog.blocks[-1].sub != 3:
        prog.warnings.append("le dernier bloc n'est pas un L3 (cycle final absent)")
    for b in prog.blocks:
        if b.angle and b.angle >= 180:      # [DOC 5.7]
            prog.warnings.append(
                f"bloc N{b.index} : cintrage a {b.angle:g}° — verifier la "
                "sequence de degagement de tete")

    if not prog.tooling:
        prog.warnings.append(
            "aucun appel d'outillage (L54, L56, L58...) : tete et diametre "
            "inconnus, repli sur le CODE_MAT de la LFT")
    if prog.diameter is None:
        prog.warnings.append(
            "diametre absent du programme : repli sur le CODE_MAT de la LFT")
    if prog.ds is None and prog.complete:
        prog.warnings.append(
            "pas de DS dans le commentaire : aucun temoin pour verifier le "
            "dernier segment")

    r6 = prog.init.get("R6")
    if r6 is not None:
        if prog.declared_length is None:
            prog.declared_length = r6
        elif abs(r6 - prog.declared_length) > 0.01:
            prog.warnings.append(
                f"R6={r6} contredit L={prog.declared_length} du commentaire"
            )
    return prog


def _rparams(fragment: str) -> dict[str, float]:
    return {f"R{k}": float(v) for k, v in RE_RPARAM.findall(fragment)}


def _parse_comment(prog: RawProgram, text: str = "") -> None:
    """Extrait ce qu'on peut d'une ligne de commentaire.

    On ne remplace jamais une valeur deja trouvee : le premier commentaire
    d'en-tete fait autorite, les suivants ne font que completer.
    """
    c = text or prog.comment
    if not c:
        return
    if not prog.machine:
        prog.machine = c.split(",")[0].strip(" (")
    if prog.diameter is None and (m := RE_DIAM.search(c)):
        prog.diameter = float(m.group(1))
    if prog.declared_length is None and (m := RE_LEN.search(c)):
        prog.declared_length = float(m.group(1))
    if prog.ds is None and (m := RE_DS.search(c)):
        prog.ds = float(m.group(1))
    if prog.recut is None and (m := RE_RECOUPE.search(c)):
        prog.recut = float(m.group(1))
