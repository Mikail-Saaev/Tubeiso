"""Traitement de masse : des milliers de LFT vers une bibliotheque rangee.

Une campagne lit un ou plusieurs dossiers de fichiers LFT et produit, pour
chaque tube retenu, son plan PDF, son modele 3D et ses donnees techniques,
ranges dans une arborescence ou tout se retrouve a coup sur :

    <sortie>/
      INDEX.xlsx                      une ligne par tube, toutes LFT confondues
      rapport.csv                     le meme contenu, en texte
      journal.txt                     ce qui s'est passe, fichier par fichier
      <GROUPE>/<MACHINE>/<LFT>/
          <LFT>_cahier.pdf            tous les plans du lot, avec sa couverture
          <LFT>_recapitulatif.csv     les pieces du lot
          plans/<REPERE>.pdf          le plan autoportant du tube
          modeles_3d/<REPERE>.stp     le solide, pour la sous-traitance
          donnees/<REPERE>.json       toutes les donnees techniques

Deux principes de fonctionnement :

* **seuls les tuyaux equipes d'une PROGCRIPPA sont traites** (voir `scope.py`).
  Les autres ne produisent aucun fichier, mais figurent dans l'index avec leur
  motif d'exclusion : une campagne rend compte de 100 % des lignes lues.

* **une campagne est reprenable.** Une LFT deja traitee est sautee, sauf
  `--force`. Un plantage sur un fichier n'interrompt pas la campagne : il est
  journalise et le fichier suivant est traite.
"""
from __future__ import annotations

import csv
import json
import os
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import bsa, conventions, geometry, lft, materials, registry, render, scope, solid, validate
from .config import Config
from .parsers import crippa

EXTENSIONS = (".xlsx", ".xlsm")
SKIP_PREFIX = ("~$", ".")


# --------------------------------------------------------------------- options

@dataclass
class Options:
    """Reglages d'une campagne."""

    config: str | None = None
    repertoire: str | None = None
    formats: tuple[str, ...] = ("step",)
    plans: bool = True
    booklet: bool = True
    models: bool = True
    dxf: bool = False
    force: bool = False
    azimuth: float | None = None
    limit: int | None = None
    workers: int = 1

    def as_dict(self) -> dict:
        d = asdict(self)
        d["formats"] = list(self.formats)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Options":
        d = dict(d)
        d["formats"] = tuple(d.get("formats") or ("step",))
        return cls(**d)


# ---------------------------------------------------------------------- resultat

@dataclass
class TubeRow:
    """Une ligne d'index. C'est le contrat de sortie de la campagne."""

    groupe: str = ""
    machine: str = ""
    designation: str = ""
    lft: str = ""
    lot: str = ""
    repere: str = ""
    programme: str = ""
    statut: str = ""
    motif: str = ""
    detail: str = ""
    nature: str = ""
    matiere: str = ""
    code_matiere: str = ""
    diametre: float | None = None
    paroi: float | None = None
    rayon: float | None = None
    coudes: int | None = None
    developpe: float | None = None
    longueur_debit: float | None = None
    recoupe: float | None = None
    controle: str = ""
    anomalies: str = ""
    plan_pdf: str = ""
    modele_3d: str = ""
    donnees: str = ""
    fichier_source: str = ""
    incident: str = ""


@dataclass
class LftResult:
    source: str
    lft: str = ""
    groupe: str = ""
    machine: str = ""
    rows: list[TubeRow] = field(default_factory=list)
    skipped: bool = False
    incident: str = ""

    @property
    def treated(self) -> int:
        return sum(1 for r in self.rows
                   if r.statut in (scope.TRAITE, scope.DROIT))

    @property
    def excluded(self) -> int:
        return sum(1 for r in self.rows if r.statut == scope.EXCLU)


@dataclass
class Campaign:
    out_dir: str
    started: str = ""
    finished: str = ""
    files: list[LftResult] = field(default_factory=list)

    @property
    def rows(self) -> list[TubeRow]:
        return [r for f in self.files for r in f.rows]

    def summary(self) -> dict:
        rows = self.rows
        motifs: dict[str, int] = {}
        controls: dict[str, int] = {}
        for r in rows:
            if r.motif:
                motifs[r.motif] = motifs.get(r.motif, 0) + 1
            if r.controle:
                controls[r.controle] = controls.get(r.controle, 0) + 1
        return {
            "fichiers": len(self.files),
            "fichiers_sautes": sum(1 for f in self.files if f.skipped),
            "fichiers_en_erreur": sum(1 for f in self.files if f.incident),
            "pieces": len(rows),
            "traitees": sum(1 for r in rows if r.statut == scope.TRAITE),
            "tubes_droits": sum(1 for r in rows if r.statut == scope.DROIT),
            "exclues": sum(1 for r in rows if r.statut == scope.EXCLU),
            "plans": sum(1 for r in rows if r.plan_pdf),
            "modeles_3d": sum(1 for r in rows if r.modele_3d),
            "motifs": dict(sorted(motifs.items(), key=lambda kv: -kv[1])),
            "controles": controls,
        }


# --------------------------------------------------------------------- decouverte

def discover(paths, recursive: bool = True) -> list[Path]:
    """Liste les LFT a traiter. Accepte des fichiers comme des dossiers."""
    out: list[Path] = []
    seen: set[str] = set()
    for raw in ([paths] if isinstance(paths, (str, Path)) else paths):
        p = Path(raw).expanduser()
        if p.is_file():
            candidates = [p]
        elif p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            candidates = sorted(q for q in it if q.is_file())
        else:
            raise FileNotFoundError(f"source introuvable : {p}")
        for q in candidates:
            if q.suffix.lower() not in EXTENSIONS:
                continue
            if q.name.startswith(SKIP_PREFIX):
                continue
            key = str(q.resolve())
            if key not in seen:
                seen.add(key)
                out.append(q)
    return out


# ------------------------------------------------------------------- traitement

def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace(os.sep, "/")
    except ValueError:                                           # pragma: no cover
        return str(path)


def _plan_data(rec, raw, tube, cl, tooling, issues, entry, lft_code, source,
               material, cfg) -> render.PlanData:
    return render.PlanData(
        tube=tube, centerline=cl, tooling=tooling, issues=issues,
        material=material,
        groupe=entry.groupe, machine=entry.machine, designation=entry.description,
        lft=lft_code, source_file=str(source),
        embout_1=str(rec.get("EMBOUT_1") or ""),
        embout_2=str(rec.get("EMBOUT_2") or ""),
        recoupe_1=float(rec.number("RECOUPE_1") or 0.0),
        recoupe_2=float(rec.number("RECOUPE_2") or 0.0),
        recoupe_programme=float((raw.recut if raw else 0) or 0.0),
        lft_length=rec.number("LONGUEUR"),
        quantite=rec.number("QTE_DEB"),
        vitesse=str(rec.get("VITESSE") or ""),
        gabarit=str(rec.get("GABARIT") or ""),
        dessin=str(rec.get("DESSIN") or ""),
        remarque=str(rec.get("REMARQUE") or ""),
        handedness=cfg.handedness, angle_mode=cfg.angle_mode,
    )


def _tube_json(data: render.PlanData, verdict: scope.Verdict,
               paths: dict) -> dict:
    tube, cl = data.tube, data.centerline
    return {
        "identification": {
            "repere": tube.ref, "programme": tube.program_number or tube.program,
            "lot": tube.list_number, "lft": data.lft,
            "groupe": data.groupe, "machine": data.machine,
            "designation": data.designation, "fichier_source": data.source_file,
        },
        "matiere": verdict.as_dict(),
        "debit": {
            "developpe_declare_R6": tube.declared_length,
            "developpe_recalcule": round(cl.developed, 3),
            "longueur_lft": data.lft_length,
            "recoupe_depart": data.recoupe_1, "recoupe_arrivee": data.recoupe_2,
            "longueur_a_debiter": round(data.cut_length, 2),
            "allongement_pct": data.tooling.elongation,
        },
        "cintrage": {
            "rayon_fibre_neutre": data.tooling.clr,
            "nombre_de_coudes": tube.n_bends,
            "mode_angle": tube.angle_mode,
            "sens_rotation": data.handedness,
            "lra": [
                {
                    "n": i + 1,
                    "longueur_amont": round(tube.straights[i], 3)
                    if i < len(tube.straights) else None,
                    "rotation_B": b.rotation, "angle_reel": b.angle,
                    "r15_programme": b.r15, "retour_elastique": b.springback,
                    "rayon": b.clr,
                }
                for i, b in enumerate(tube.bends)
            ],
            "dernier_segment": tube.straights[-1] if tube.straights else None,
        },
        "geometrie": {
            "sommets_xyz": [[round(float(v), 3) for v in p] for p in cl.vertices],
            "tangences_xyz": [[round(float(v), 3) for v in p]
                              for p in cl.tangent_points],
            "encombrement": [round(float(v), 2) for v in cl.envelope],
        },
        "extremites": {
            "embout_depart": data.embout_1,
            "embout_arrivee": data.embout_2,
            "libelle_depart": render.fitting_label(data.embout_1, data.diameter)[0],
            "libelle_arrivee": render.fitting_label(data.embout_2, data.diameter)[0],
        },
        "controles": [
            {"niveau": i.level, "code": i.code, "message": i.message,
             "source": i.source}
            for i in data.issues
        ],
        "statut_controle": data.status,
        "programme_iso": tube.source,
        "fichiers": paths,
        "genere_le": datetime.now().isoformat(timespec="seconds"),
        "genere_par": "tubeiso",
    }


def process_file(source, out_root, options: Options,
                 entry: registry.Entry | None = None) -> LftResult:
    """Traite une LFT complete. Ne leve pas : tout incident est capture."""
    source = Path(source)
    out_root = Path(out_root)
    entry = entry or registry.parse_filename(source.stem)
    lft_code = source.stem
    result = LftResult(source=str(source), lft=lft_code,
                       groupe=entry.groupe, machine=entry.machine)

    lot_dir = (out_root / registry.safe_name(entry.groupe)
               / registry.safe_name(entry.machine) / registry.safe_name(lft_code))
    marker = lot_dir / ".tubeiso-ok"
    if marker.exists() and not options.force:
        result.skipped = True
        return result

    try:
        cfg = Config.load(options.config)
        book = lft.read(source)
    except Exception as exc:
        result.incident = f"{type(exc).__name__}: {exc}"
        return result

    conv = conventions.get(cfg.convention)
    plans_dir, models_dir, data_dir = (lot_dir / "plans", lot_dir / "modeles_3d",
                                       lot_dir / "donnees")
    made: list[render.PlanData] = []
    excluded: list[dict] = []

    for lot in book.lots:
        for rec in lot.tubes:
            row = TubeRow(
                groupe=entry.groupe, machine=entry.machine,
                designation=entry.description, lft=lft_code,
                lot=rec.list_number or registry.list_number_from(lft_code),
                repere=rec.rep or rec.program_number or rec.key,
                programme=rec.program_number,
                fichier_source=str(source),
            )
            raw = None
            if scope.has_program(rec.iso):
                try:
                    raw = crippa.parse(rec.iso, ref=rec.rep)
                except Exception as exc:                          # pragma: no cover
                    row.incident = f"parseur : {exc}"
            verdict = scope.evaluate(rec, raw)
            row.statut, row.motif, row.detail = (verdict.status, verdict.reason,
                                                 verdict.detail)
            # `motif` ne sert qu'aux exclusions : une piece qui sort des
            # fichiers n'a pas de motif, sinon les comptes ne tombent plus.
            if verdict.ok:
                row.motif = ""
            row.nature = verdict.kind
            if verdict.material:
                row.matiere = verdict.material.designation
                row.code_matiere = verdict.material.code
                row.paroi = verdict.material.wall
            row.diametre = verdict.diameter

            if not verdict.ok:
                excluded.append({"repere": row.repere, "motif": row.motif,
                                 "detail": row.detail})
                result.rows.append(row)
                continue

            try:
                data, issues = _build(rec, raw, verdict, cfg, conv, entry,
                                      lft_code, source)
            except Exception as exc:
                row.statut, row.motif = scope.EXCLU, "échec_géométrie"
                row.detail = f"{type(exc).__name__}: {exc}"
                row.incident = traceback.format_exc(limit=2)
                excluded.append({"repere": row.repere, "motif": row.motif,
                                 "detail": row.detail})
                result.rows.append(row)
                continue

            row.rayon = data.tooling.clr
            row.coudes = data.tube.n_bends
            row.developpe = round(data.centerline.developed, 1)
            row.longueur_debit = round(data.cut_length, 1)
            row.recoupe = data.recut or None
            row.controle = data.status
            row.anomalies = " | ".join(
                f"{i.level}:{i.code}" for i in issues if i.level != "info") or ""

            # Le nom porte la LFT d'origine : un fichier exporte doit se
            # rattacher a sa source sans qu'on ait rien a ouvrir.
            base = registry.output_basename(lft_code, row.repere)
            paths: dict[str, str] = {}

            if options.plans:
                try:
                    pdf = render.to_pdf(data, plans_dir / f"{base}.pdf",
                                        options.azimuth)
                    row.plan_pdf = _relative(pdf, out_root)
                    paths["plan_pdf"] = row.plan_pdf
                except Exception as exc:
                    row.incident = f"plan : {type(exc).__name__}: {exc}"

            if options.models:
                try:
                    written = solid.export(
                        data.tube, data.centerline, models_dir,
                        tooling=data.tooling, formats=options.formats,
                        basename=base)
                    for p in written:
                        rel = _relative(p, out_root)
                        paths.setdefault(p.suffix.lstrip("."), rel)
                        if p.suffix.lower() in (".stp", ".step"):
                            row.modele_3d = rel
                except Exception as exc:
                    row.incident = (row.incident + " · " if row.incident else "") + \
                        f"3D : {type(exc).__name__}: {exc}"

            if options.dxf:
                try:
                    dxf_path = models_dir / f"{base}.dxf"
                    dxf_path.parent.mkdir(parents=True, exist_ok=True)
                    render.to_dxf(data.tube, data.centerline, str(dxf_path))
                    paths["dxf"] = _relative(dxf_path, out_root)
                except Exception as exc:                          # pragma: no cover
                    row.incident = (row.incident + " · " if row.incident else "") + \
                        f"DXF : {exc}"

            data_dir.mkdir(parents=True, exist_ok=True)
            json_path = data_dir / f"{base}.json"
            json_path.write_text(
                json.dumps(_tube_json(data, verdict, paths), ensure_ascii=False,
                           indent=2), encoding="utf-8")
            row.donnees = _relative(json_path, out_root)

            made.append(data)
            result.rows.append(row)

    if made and options.booklet:
        try:
            render.booklet(made, lot_dir / f"{registry.safe_name(lft_code)}_cahier.pdf",
                           lot_label=lft_code, excluded=excluded)
        except Exception as exc:                                  # pragma: no cover
            result.incident = f"cahier : {type(exc).__name__}: {exc}"

    if result.rows:
        _write_csv(lot_dir / f"{registry.safe_name(lft_code)}_recapitulatif.csv",
                   result.rows)
        lot_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now().isoformat(timespec="seconds"),
                          encoding="utf-8")
    return result


def _build(rec, raw, verdict, cfg, conv, entry, lft_code, source):
    """Construit la piece, sa geometrie et ses controles."""
    if verdict.straight:
        # Pas de programme : une droite, une longueur, un diametre. [Info Crippa]
        tooling = cfg.for_program("", verdict.diameter, rec.get("CODE_MAT"))
        length = float(rec.number("LONGUEUR") or 0.0)
        tube = conv.build_straight(rec.rep or rec.program_number or rec.key,
                                   length, verdict.diameter or tooling.diameter,
                                   tooling)
        tube.list_number = rec.list_number
        tube.program_number = rec.program_number
        tube.warnings.extend(rec.warnings)
        cl = geometry.build(tube, handedness=cfg.handedness)
        issues = validate.check(tube, tooling, centerline=cl,
                                length_tol=cfg.tolerance, lft_length=length)
        data = _plan_data(rec, None, tube, cl, tooling, issues, entry, lft_code,
                          source, verdict.material, cfg)
        return data, issues

    if raw.declared_length is None and rec.number("LONGUEUR"):
        raw.declared_length = float(rec.number("LONGUEUR"))
    if raw.diameter is None and verdict.diameter:
        raw.diameter = float(verdict.diameter)

    tooling = cfg.for_program(raw.tooling, raw.diameter, rec.get("CODE_MAT"))
    recut = rec.recut or float(raw.recut or 0.0)
    tube = conv.build(raw, tooling, recut=recut, angle_mode=cfg.angle_mode)
    tube.ref = rec.rep or rec.program_number or tube.ref
    tube.list_number = rec.list_number
    tube.program_number = rec.program_number
    tube.warnings.extend(rec.warnings)

    cl = geometry.build(tube, handedness=cfg.handedness)
    issues = validate.check(tube, tooling, centerline=cl, recut=recut,
                            length_tol=cfg.tolerance,
                            lft_length=rec.number("LONGUEUR"))
    data = _plan_data(rec, raw, tube, cl, tooling, issues, entry, lft_code,
                      source, verdict.material, cfg)
    return data, issues


# ------------------------------------------------------------------------ sortie

def _write_csv(path: Path, rows: list[TubeRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(TubeRow().__dict__.keys())
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow(r.__dict__)


def _worker(payload):                                            # pragma: no cover
    src, out_root, opts, ent = payload
    return process_file(src, out_root, Options.from_dict(opts),
                        registry.Entry(*ent))


def run(sources, out_dir, options: Options | None = None, on_file=None,
        cancel=None) -> Campaign:
    """Lance une campagne.

    `on_file(i, total, LftResult)` suit l'avancement, `cancel()` permet de
    l'interrompre proprement entre deux fichiers : une campagne de plusieurs
    heures doit pouvoir etre arretee sans perdre ce qui est deja ecrit.
    """
    options = options or Options()
    out_root = Path(out_dir).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)

    reg = registry.Registry.load(options.repertoire)
    files = discover(sources)
    if options.limit:
        files = files[: options.limit]

    campaign = Campaign(out_dir=str(out_root),
                        started=datetime.now().isoformat(timespec="seconds"))
    total = len(files)

    if options.workers and options.workers > 1 and total > 1:    # pragma: no cover
        from concurrent.futures import ProcessPoolExecutor
        payloads = [
            (str(f), str(out_root), options.as_dict(),
             (reg.resolve(f).groupe, reg.resolve(f).machine,
              reg.resolve(f).description, reg.resolve(f).source))
            for f in files
        ]
        with ProcessPoolExecutor(max_workers=options.workers) as pool:
            for i, res in enumerate(pool.map(_worker, payloads), start=1):
                campaign.files.append(res)
                if on_file:
                    on_file(i, total, res)
                if cancel and cancel():
                    break
    else:
        for i, f in enumerate(files, start=1):
            if cancel and cancel():
                break
            res = process_file(f, out_root, options, reg.resolve(f))
            campaign.files.append(res)
            if on_file:
                on_file(i, total, res)

    campaign.finished = datetime.now().isoformat(timespec="seconds")
    _write_csv(out_root / "rapport.csv", campaign.rows)
    _write_journal(out_root / "journal.txt", campaign, reg)
    try:
        write_index(out_root / "INDEX.xlsx", campaign, reg)
    except Exception as exc:                                     # pragma: no cover
        (out_root / "journal.txt").open("a", encoding="utf-8").write(
            f"\nINDEX.xlsx non écrit : {type(exc).__name__}: {exc}\n")
    return campaign


def _write_journal(path: Path, campaign: Campaign, reg: registry.Registry) -> None:
    s = campaign.summary()
    lines = [
        "CAMPAGNE TUBEISO",
        f"début   : {campaign.started}",
        f"fin     : {campaign.finished}",
        f"sortie  : {campaign.out_dir}",
        f"répertoire machines : {reg.path or 'non fourni (rattachement par nom de fichier)'}",
        "",
        f"fichiers LFT lus      : {s['fichiers']}"
        f"   (sautés : {s['fichiers_sautes']}, en erreur : {s['fichiers_en_erreur']})",
        f"pièces rencontrées    : {s['pieces']}",
        f"pièces cintrées       : {s['traitees']}",
        f"tubes droits          : {s['tubes_droits']}",
        f"pièces hors périmètre : {s['exclues']}",
        f"plans PDF écrits      : {s['plans']}",
        f"modèles 3D écrits     : {s['modeles_3d']}",
        "",
        "Motifs d'exclusion :",
    ]
    for motif, n in s["motifs"].items():
        lines.append(f"  {n:6d}  {motif:26s} {scope.LIBELLES.get(motif, '')}")
    lines += ["", "Contrôle des pièces traitées :"]
    for k, n in sorted(s["controles"].items()):
        lines.append(f"  {n:6d}  {k}")
    incidents = [f for f in campaign.files if f.incident]
    if incidents:
        lines += ["", "Incidents :"]
        for f in incidents:
            lines.append(f"  {Path(f.source).name} — {f.incident}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------- index Excel

INDEX_COLUMNS = [
    ("groupe", "Groupe", 12), ("machine", "Machine", 30),
    ("designation", "Désignation machine", 34), ("lft", "LFT", 34),
    ("lot", "Lot / LISTE", 16), ("repere", "Repère", 12),
    ("programme", "Programme", 18), ("statut", "Statut", 10),
    ("motif", "Motif d'exclusion", 24), ("nature", "Nature", 10),
    ("matiere", "Matière", 24), ("code_matiere", "Code BSA", 14),
    ("diametre", "Ø ext", 8), ("paroi", "Paroi", 8), ("rayon", "Rm", 8),
    ("coudes", "Coudes", 8), ("developpe", "Développé", 11),
    ("longueur_debit", "À débiter", 11), ("recoupe", "Recoupe", 9),
    ("controle", "Contrôle", 11), ("anomalies", "Anomalies", 34),
    ("plan_pdf", "Plan PDF", 40), ("modele_3d", "Modèle 3D", 40),
    ("donnees", "Données", 40), ("incident", "Incident", 30),
]


def write_index(path, campaign: Campaign, reg: registry.Registry | None = None):
    """Ecrit INDEX.xlsx : une ligne par tube, filtrable, avec les liens."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    path = Path(path)
    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="0F5F7A")
    link_font = Font(color="0F5F7A", underline="single")
    colours = {"CONFORME": "E9F3ED", "ALERTE": "FBF2E0", "ERREUR": "FAE9E7"}

    # --- feuille Tubes
    ws = wb.active
    ws.title = "Tubes"
    for j, (_, title, width) in enumerate(INDEX_COLUMNS, start=1):
        c = ws.cell(row=1, column=j, value=title)
        c.font, c.fill = head_font, head_fill
        c.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(j)].width = width
    ws.freeze_panes = "A2"

    link_cols = {"plan_pdf", "modele_3d", "donnees"}
    for i, row in enumerate(campaign.rows, start=2):
        fill = colours.get(row.controle)
        for j, (key, _, _) in enumerate(INDEX_COLUMNS, start=1):
            value = getattr(row, key)
            c = ws.cell(row=i, column=j, value=value if value != "" else None)
            if key in link_cols and value:
                c.hyperlink = value
                c.font = link_font
            if fill and key in ("controle",):
                c.fill = PatternFill("solid", fgColor=fill)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(INDEX_COLUMNS))}{max(ws.max_row, 1)}"

    # --- feuille LFT
    ws2 = wb.create_sheet("LFT")
    heads = ["Groupe", "Machine", "LFT", "Pièces", "Traitées", "Exclues",
             "Cahier PDF", "Fichier source", "Incident"]
    for j, h in enumerate(heads, start=1):
        c = ws2.cell(row=1, column=j, value=h)
        c.font, c.fill = head_font, head_fill
        ws2.column_dimensions[get_column_letter(j)].width = [12, 30, 36, 9, 10, 9, 44, 60, 30][j - 1]
    ws2.freeze_panes = "A2"
    for i, f in enumerate(campaign.files, start=2):
        cahier = ""
        if f.treated:
            cahier = (f"{registry.safe_name(f.groupe)}/{registry.safe_name(f.machine)}/"
                      f"{registry.safe_name(f.lft)}/{registry.safe_name(f.lft)}_cahier.pdf")
        for j, v in enumerate([f.groupe, f.machine, f.lft, len(f.rows), f.treated,
                               f.excluded, cahier, f.source, f.incident], start=1):
            c = ws2.cell(row=i, column=j, value=v if v != "" else None)
            if j == 7 and cahier:
                c.hyperlink, c.font = cahier, link_font
    ws2.auto_filter.ref = f"A1:I{max(ws2.max_row, 1)}"

    # --- feuille Campagne
    ws3 = wb.create_sheet("Campagne")
    s = campaign.summary()
    ws3.column_dimensions["A"].width = 34
    ws3.column_dimensions["B"].width = 16
    ws3.column_dimensions["C"].width = 60
    rows = [
        ("Campagne tubeiso", ""), ("Début", campaign.started),
        ("Fin", campaign.finished), ("Dossier de sortie", campaign.out_dir),
        ("Répertoire machines", (reg.path if reg else "") or "rattachement par nom de fichier"),
        ("", ""),
        ("Fichiers LFT lus", s["fichiers"]), ("Fichiers sautés", s["fichiers_sautes"]),
        ("Fichiers en erreur", s["fichiers_en_erreur"]),
        ("Pièces rencontrées", s["pieces"]), ("Pièces cintrées", s["traitees"]),
        ("Tubes droits", s["tubes_droits"]),
        ("Pièces hors périmètre", s["exclues"]),
        ("Plans PDF", s["plans"]), ("Modèles 3D", s["modeles_3d"]),
        ("", ""), ("Motifs d'exclusion", ""),
    ]
    for i, (k, v) in enumerate(rows, start=1):
        ws3.cell(row=i, column=1, value=k).font = Font(bold=not v or k.startswith("Campagne"))
        ws3.cell(row=i, column=2, value=v)
    r = len(rows) + 1
    for motif, n in s["motifs"].items():
        ws3.cell(row=r, column=1, value=motif)
        ws3.cell(row=r, column=2, value=n)
        ws3.cell(row=r, column=3, value=scope.LIBELLES.get(motif, ""))
        r += 1
    r += 1
    ws3.cell(row=r, column=1, value="Contrôle des pièces traitées").font = Font(bold=True)
    for k, n in sorted(s["controles"].items()):
        r += 1
        ws3.cell(row=r, column=1, value=k)
        ws3.cell(row=r, column=2, value=n)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


__all__ = ["Options", "TubeRow", "LftResult", "Campaign", "discover",
           "process_file", "run", "write_index"]
