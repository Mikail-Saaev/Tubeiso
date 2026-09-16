"""Serveur applicatif.

L'interface est une page web servie en local. Ce choix n'est pas un pis-aller :
il garantit que le rendu 3D fonctionne sur n'importe quelle machine (le moteur
WebGL du navigateur est deja installe partout), evite tout probleme de pilote
OpenGL ou de contexte graphique, et rend l'application identique sous Windows,
macOS et Linux.

Le serveur n'ecoute que sur 127.0.0.1 : rien n'est expose sur le reseau.

Deux notions distinctes, et jamais melangees dans l'API :

    lot / LISTE   le numero de LFT           0792-0002-JV
    programme     le numero de programme     792_JV-412

Une piece est identifiee par un `uid` stable, et non par son repere : le meme
repere peut exister dans deux lots differents du meme fichier.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import shutil
import sys
import subprocess
import tempfile
import threading
from datetime import datetime
import traceback
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .. import (batch, bsa, conventions, geometry, lft, materials, registry,
                render, scope, solid, stepreader, validate)
from ..config import Config, code_mat_diameter
from ..parsers import crippa

STATIC = Path(__file__).with_name("static")
app_log = logging.getLogger("tubeiso")


def _group_reasons(failed: list[dict]) -> list[dict]:
    """Regroupe les echecs par motif : une meme cause se lit une fois."""
    groups: dict[str, list[str]] = {}
    for f in failed:
        groups.setdefault(f.get("error", "?"), []).append(str(f.get("ref", "?")))
    return [{"error": k, "count": len(v), "refs": v[:12]}
            for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
MESH_CACHE_MAX = 24          # pieces gardees en memoire, maillage compris


class Piece:
    """Une piece prete a servir : donnees LFT, programme, geometrie, controles."""

    def __init__(self, uid: str, record: lft.TubeRecord, lot: str):
        self.uid = uid
        self.record = record
        self.lot = lot
        self.raw: crippa.RawProgram | None = None
        self.tube = None
        self.tooling = None
        self.recut = 0.0
        self.issues: list = []
        self.verdict: scope.Verdict | None = None

    @property
    def in_scope(self) -> bool:
        """La piece produit au moins un fichier."""
        return bool(self.verdict and self.verdict.ok)

    @property
    def cut_only(self) -> bool:
        """Forme non definie : fiche de debit, jamais de plan ni de 3D."""
        return bool(self.verdict and self.verdict.cut_only)

    @property
    def no_3d(self) -> str:
        """Motif qui interdit le modele 3D, ou chaine vide."""
        if self.cut_only:
            return self.verdict.reason
        return validate.unsafe(self.issues)

    @property
    def ref(self) -> str:
        return self.record.rep or self.record.program_number or self.uid


class CampaignRunner:
    """Une campagne lancee depuis l'interface, suivie en direct.

    Le traitement de milliers de LFT ne peut pas bloquer la requete HTTP : il
    tourne dans un fil separe, et l'interface interroge `state()`. Le fil est
    unique — deux campagnes simultanees ecriraient dans la meme arborescence.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.cancel = threading.Event()
        self.reset()

    def reset(self) -> None:
        self.running = False
        self.done = 0
        self.total = 0
        self.current = ""
        self.out_dir = ""
        self.started = ""
        self.finished = ""
        self.error = ""
        self.summary: dict = {}
        self.lines: list[str] = []

    @property
    def busy(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, sources, out_dir, options) -> dict:
        if self.busy:
            raise RuntimeError("une campagne est déjà en cours")
        files = batch.discover(sources)
        if options.limit:
            files = files[: options.limit]
        if not files:
            raise ValueError("aucun fichier .xlsx ou .xlsm trouvé dans ce dossier")
        self.reset()
        self.cancel.clear()
        self.running = True
        self.total = len(files)
        self.out_dir = str(out_dir)
        self.started = datetime.now().isoformat(timespec="seconds")
        self.thread = threading.Thread(
            target=self._run, args=(sources, out_dir, options), daemon=True)
        self.thread.start()
        return self.state()

    def _run(self, sources, out_dir, options) -> None:
        def progress(i, total, res):
            with self.lock:
                self.done, self.total = i, total
                self.current = Path(res.source).name
                state = ("sauté" if res.skipped else
                         f"ERREUR — {res.incident}" if res.incident else
                         f"{res.treated} traitée(s), {res.excluded} exclue(s)")
                self.lines.append(f"{Path(res.source).name} — {state}")
                del self.lines[:-400]

        try:
            campaign = batch.run(sources, out_dir, options, on_file=progress,
                                 cancel=self.cancel.is_set)
            with self.lock:
                self.summary = campaign.summary()
        except Exception as exc:                                 # pragma: no cover
            logging.getLogger("tubeiso").exception("campagne")
            with self.lock:
                self.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self.lock:
                self.running = False
                self.finished = datetime.now().isoformat(timespec="seconds")

    def stop(self) -> dict:
        self.cancel.set()
        return self.state()

    def state(self) -> dict:
        with self.lock:
            return {
                "running": self.running or self.busy,
                "cancelling": self.cancel.is_set(),
                "done": self.done, "total": self.total,
                "current": self.current, "out_dir": self.out_dir,
                "started": self.started, "finished": self.finished,
                "error": self.error, "summary": self.summary,
                "lines": self.lines[-60:],
                "index": (str(Path(self.out_dir) / "INDEX.xlsx")
                          if self.out_dir else ""),
            }


class Session:
    """Etat courant : le fichier ouvert et les pieces qu'il contient."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.config = Config.load(None)
        self.source: Path | None = None
        self.book: lft.Workbook | None = None
        self.pieces: dict[str, Piece] = {}
        self.workdir = tempfile.mkdtemp(prefix="tubeiso-")
        self._cache: dict[tuple, dict] = {}

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    # ------------------------------------------------------------- invalidation
    @property
    def config_key(self) -> str:
        blob = json.dumps(self.config.data, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]

    def invalidate(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------ chargement
    def open_lft(self, path: str, sheet: str | None = None) -> dict:
        book = lft.read(path, sheet)
        pieces: dict[str, Piece] = {}
        for i, lot in enumerate(book.lots):
            for j, rec in enumerate(lot.tubes):
                uid = f"{i}-{j}"
                pieces[uid] = Piece(uid, rec, lot.number)

        with self.lock:
            self.source = Path(path)
            self.book = book
            self.pieces = pieces
            self.invalidate()
            for p in pieces.values():
                self._build(p)
        return self.summary()

    def _centerline(self, tube):
        """Fibre neutre, ou None. Le controle de longueur doit s'appuyer sur
        elle : c'est elle qui sert a exporter le solide."""
        try:
            return geometry.build(tube, handedness=self.config.handedness)
        except Exception:
            return None

    def _build(self, p: Piece) -> None:
        """Perimetre -> programme -> geometrie -> controles, pour une piece."""
        rec = p.record
        conv = conventions.get(self.config.convention)
        lft_length = rec.number("LONGUEUR")

        raw = crippa.parse(rec.iso, ref=p.ref) if scope.has_program(rec.iso) else None
        p.verdict = scope.evaluate(rec, raw)
        diameter = p.verdict.diameter or code_mat_diameter(rec.get("CODE_MAT"))

        if p.verdict.straight:
            # Rigide, sans coudes : une droite et un diametre suffisent.
            # [Info Crippa] « meme s'il n'y a pas de programme, il faut generer
            # la 3D avec uniquement la longueur et le diametre. »
            tooling = scope.tooling_for(self.config, p.verdict, rec)
            length = float(p.verdict.length or lft_length or 0.0)
            p.raw = raw
            p.tooling = tooling
            p.recut = rec.recut
            p.tube = conv.build_straight(p.ref, length,
                                         diameter or tooling.diameter, tooling)
            p.tube.list_number = rec.list_number
            p.tube.program_number = rec.program_number
            p.tube.warnings.extend(rec.warnings)
            p.issues = validate.check(p.tube, tooling,
                                      centerline=self._centerline(p.tube),
                                      length_tol=self.config.tolerance,
                                      lft_length=lft_length)
            return

        if not p.verdict.modelled:
            # Forme non definie ou piece sans livrable : AUCUNE geometrie n'est
            # fabriquee, pas meme pour l'affichage. La version precedente
            # construisait un tube droit « pour avoir quelque chose a montrer »
            # — et un tuyau souple de 5 m s'affichait en 3D avec un developpe,
            # ce qu'un operateur pouvait prendre pour une piece reelle.
            p.raw = raw
            p.tooling = scope.tooling_for(self.config, p.verdict, rec)
            p.recut = rec.recut
            p.tube = None
            level = validate.WARN if p.verdict.ok else validate.ERROR
            code = "forme_non_definie" if p.verdict.cut_only else "sans_livrable"
            p.issues = [validate.Issue(
                level, code,
                f"{p.verdict.reason} — {p.verdict.detail}", "périmètre")]
            return

        raw.name = raw.name or p.ref
        if raw.declared_length is None and lft_length:
            raw.declared_length = float(lft_length)
        if raw.diameter is None and diameter:
            raw.diameter = float(diameter)
        tooling = self.config.for_program(raw.tooling, raw.diameter,
                                          rec.get("CODE_MAT"))
        p.recut = rec.recut or float(raw.recut or 0.0)
        p.raw = raw
        p.tube = conv.build(raw, tooling, recut=p.recut,
                            angle_mode=self.config.angle_mode)

        p.tube.ref = p.ref
        p.tube.list_number = rec.list_number
        p.tube.program_number = rec.program_number
        p.tooling = tooling
        p.tube.warnings.extend(rec.warnings)
        p.issues = validate.check(p.tube, tooling,
                                  centerline=self._centerline(p.tube),
                                  recut=p.recut,
                                  length_tol=self.config.tolerance,
                                  lft_length=lft_length)

    def rebuild(self) -> dict:
        """Rejoue tout le fichier avec la configuration courante."""
        with self.lock:
            self.invalidate()
            for p in self.pieces.values():
                self._build(p)
        return self.summary()

    # --------------------------------------------------------------- resume
    def summary(self) -> dict:
        lots = []
        for lot in (self.book.lots if self.book else []):
            items = []
            for p in self.pieces.values():
                if p.lot != lot.number:
                    continue
                t, raw = p.tube, p.raw
                hors = t is None
                mat = p.verdict.material if p.verdict else None
                items.append({
                    "uid": p.uid,
                    "ref": p.ref,
                    "lot": p.lot,
                    "programme": p.record.program_number,
                    "liste": p.record.list_number,
                    "scope": p.verdict.status if p.verdict else "",
                    "reason": p.verdict.reason if p.verdict else "",
                    "reason_label": p.verdict.detail if p.verdict else "",
                    "nature": p.verdict.kind if p.verdict else "inconnue",
                    "matiere": mat.designation if mat else None,
                    "code_mat": mat.code if mat else None,
                    "diameter": (p.verdict.diameter if hors else t.diameter) or 0,
                    "tooling": raw.tooling if raw else "",
                    "head": bsa.HEAD_NAMES.get(raw.head if raw else None, "—"),
                    "declared": None if hors else t.declared_length,
                    "bends": 0 if hors else t.n_bends,
                    "recut": p.recut,
                    "complete": True if hors else t.complete,
                    "straight": bool(p.verdict and p.verdict.straight),
                    "cut_only": p.cut_only,
                    "no_3d": p.no_3d,
                    "livrable": (p.verdict.as_dict()["livrable"]
                                 if p.verdict else "aucun"),
                    "rows": len(p.record.rows),
                    "status": ("exclue" if not p.in_scope else
                               "débit" if p.cut_only else
                               validate.worst(p.issues)),
                })
            lots.append({"number": lot.number, "label": lot.label,
                         "count": len(items), "tubes": items})
        return {
            "source": str(self.source) if self.source else None,
            "sheets": self.book.sheets_read if self.book else [],
            "columns": self.book.columns if self.book else [],
            "warnings": self.book.warnings if self.book else [],
            "lots": lots,
            "count": sum(l["count"] for l in lots),
        }

    # -------------------------------------------------------------- geometrie
    def detail(self, uid: str, deflection: float = 0.04) -> dict:
        p = self.pieces.get(uid)
        if p is None:
            raise KeyError(f"piece {uid} inconnue")
        key = (uid, round(deflection, 4), self.config_key)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        data = self._detail(p, deflection)
        if len(self._cache) >= MESH_CACHE_MAX:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = data
        return data

    def _detail(self, p: Piece, deflection: float) -> dict:
        if not p.in_scope or p.tube is None:
            return self._detail_out_of_scope(p)
        tube, tooling = p.tube, p.tooling
        cl = geometry.build(tube, handedness=self.config.handedness)

        mesh = mesh_error = None
        try:
            shp = solid.build_solid(tube, cl, tooling.wall)
            mesh = stepreader.tessellate(shp, deflection)
        except Exception as exc:
            mesh_error = str(exc)

        prims = [{
            "kind": pr.kind,
            "start": [round(float(v), 4) for v in pr.start],
            "end": [round(float(v), 4) for v in pr.end],
            "mid": None if pr.mid is None else [round(float(v), 4) for v in pr.mid],
            "centre": None if pr.centre is None else [round(float(v), 4) for v in pr.centre],
            "radius": round(pr.radius, 4),
            "angle": round(pr.angle, 4),
            "length": round(pr.length, 4),
        } for pr in cl.primitives]

        bends = [{"angle": round(b.angle, 4), "r15": b.r15,
                  "springback": round(b.springback, 4),
                  "rotation": b.rotation, "clr": b.clr} for b in tube.bends]

        return {
            "uid": p.uid,
            "ref": p.ref,
            "lot": p.lot,
            "programme": p.record.program_number,
            "liste": p.record.list_number,
            "diameter": tube.diameter,
            "wall": (p.verdict.material.wall if p.verdict and p.verdict.material
                     else tooling.wall),
            "material": tooling.material,
            "matiere": p.verdict.as_dict() if p.verdict else None,
            "bend_radius": tooling.clr,
            "tooling": p.raw.tooling if p.raw else "",
            "head": bsa.HEAD_NAMES.get(p.raw.head if p.raw else None, "—"),
            "comment": tube.comment,
            "source": tube.source,
            "declared": tube.declared_length,
            "ds": tube.ds,
            "recut": p.recut,
            "straight": tube.straight,
            "angle_mode": tube.angle_mode,
            "developed": round(cl.developed, 4),
            "straights": [round(s, 4) for s in tube.straights],
            "bends": bends,
            "primitives": prims,
            "polyline": [[round(float(v), 4) for v in q] for q in cl.points],
            "vertices": [[round(float(v), 4) for v in q] for q in cl.vertices],
            "tangents": [[round(float(v), 4) for v in q] for q in cl.tangent_points],
            "bbox": {"size": [round(float(v), 3) for v in cl.envelope],
                     "min": [round(float(v), 3) for v in cl.bbox[0]],
                     "max": [round(float(v), 3) for v in cl.bbox[1]]},
            "mesh": mesh,
            "mesh_error": mesh_error,
            "issues": [{"level": i.level, "code": i.code, "message": i.message,
                        "source": i.source} for i in p.issues],
            "status": validate.worst(p.issues),
            "no_3d": p.no_3d,
            "livrable": p.verdict.as_dict()["livrable"] if p.verdict else "",
            "notes": list(p.verdict.notes) if p.verdict else [],
            "fields": self.fields(p),
            "simulation": {
                "straights": [round(v, 4) for v in tube.straights],
                "bends": bends,
                "handedness": self.config.handedness,
                "blank_length": round(cl.developed, 3),
                "cut_length": tube.declared_length,
            },
        }

    def _detail_out_of_scope(self, p: Piece) -> dict:
        """Ce qu'on affiche d'une piece sans forme : son identite, sa matiere,
        sa longueur et le motif. Pas de geometrie, pas de developpe, pas de
        maillage — et le livrable annonce, pour qu'on sache quoi en attendre."""
        v = p.verdict
        return {
            "uid": p.uid, "ref": p.ref, "lot": p.lot,
            "programme": p.record.program_number,
            "liste": p.record.list_number,
            "out_of_scope": True,
            "cut_only": p.cut_only,
            "no_3d": p.no_3d,
            "livrable": v.as_dict()["livrable"] if v else "aucun",
            "scope": v.status if v else "exclue",
            "reason": v.reason if v else "",
            "reason_label": v.detail if v else "",
            "notes": list(v.notes) if v else [],
            "matiere": v.as_dict() if v else None,
            "diameter": (v.diameter if v else None) or 0,
            "wall": v.material.wall if v and v.material else None,
            "material": v.material.designation if v and v.material else None,
            "source": p.record.iso or "",
            "comment": "",
            "declared": (v.length if v else None) or p.record.number("LONGUEUR"),
            "quantite": p.record.number("QTE_DEB"),
            "straights": [], "bends": [], "primitives": [],
            "polyline": [], "vertices": [], "tangents": [],
            "mesh": None, "mesh_error": None,
            "issues": [{"level": i.level, "code": i.code, "message": i.message,
                        "source": i.source} for i in p.issues],
            "status": "débit" if p.cut_only else "exclue",
            "fields": self.fields(p),
            "simulation": None,
        }

    def fields(self, p: Piece) -> list[dict]:
        """Toutes les colonnes de la LFT pour cette piece, sans exception.

        C'est la garantie qu'aucune information de l'Excel n'est perdue :
        l'interface les affiche telles quelles, y compris les colonnes que
        l'application n'exploite pas.
        """
        out = []
        for col, f in p.record.fields.items():
            out.append({
                "column": col,
                "values": [str(v) for v in f.values],
                "rows": f.rows,
                "conflict": f.conflicting,
            })
        out.sort(key=lambda x: x["column"])
        return out

    # ------------------------------------------------------------------ plan
    def plan_data(self, p: Piece, cl) -> render.PlanData:
        """Rassemble ce qui figure sur le plan, y compris le rattachement."""
        entry = registry.parse_filename(Path(self.source).stem) if self.source \
            else registry.Entry("", "", "", "nom de fichier")
        rec = p.record
        return render.PlanData(
            tube=p.tube, centerline=cl, tooling=p.tooling, issues=p.issues,
            material=p.verdict.material if p.verdict else None,
            groupe=entry.groupe, machine=entry.machine,
            designation=entry.description,
            lft=Path(self.source).stem if self.source else "",
            source_file=str(self.source or ""),
            embout_1=str(rec.get("EMBOUT_1") or ""),
            embout_2=str(rec.get("EMBOUT_2") or ""),
            recoupe_1=float(rec.number("RECOUPE_1") or 0.0),
            recoupe_2=float(rec.number("RECOUPE_2") or 0.0),
            recoupe_programme=float((p.raw.recut if p.raw else 0) or 0.0),
            lft_length=rec.number("LONGUEUR"),
            quantite=rec.number("QTE_DEB"),
            remarque=str(rec.get("REMARQUE") or ""),
            handedness=self.config.handedness,
            angle_mode=self.config.angle_mode,
        )

    def debit_data(self, p: Piece) -> render.PlanData:
        """Donnees d'une fiche de debit : un tube droit fictif, non exporte.

        Il n'existe que pour n'avoir qu'un seul chemin vers la mise en page.
        La fiche porte un bandeau qui dit que la forme n'est pas definie, et
        aucun modele 3D n'est ecrit a partir de lui.
        """
        conv = conventions.get(self.config.convention)
        rec, v = p.record, p.verdict
        tooling = p.tooling or scope.tooling_for(self.config, v, rec)
        length = float((v.length if v else None) or rec.number("LONGUEUR") or 0.0)
        tube = conv.build_straight(p.ref, length,
                                   (v.diameter if v else None) or 0.0, tooling)
        tube.list_number = rec.list_number
        tube.program_number = rec.program_number
        tube.comment = (v.detail if v else "") or "forme non définie"
        tube.straight = False
        cl = geometry.build(tube, handedness=self.config.handedness) \
            if length > 0 else None
        keep_tube, keep_tooling = p.tube, p.tooling
        p.tube, p.tooling = tube, tooling
        try:
            data = self.plan_data(p, cl)
        finally:
            p.tube, p.tooling = keep_tube, keep_tooling
        data.issues = []
        return data

    # ---------------------------------------------------------------- exports
    def export(self, uids: list[str], out_dir: str, formats: list[str],
               by_type: bool = True) -> dict:
        """Ecrit les fichiers demandes. `by_type` range chaque format dans son
        propre dossier — plans/, step/, donnees/… — comme le fait une campagne."""
        out = Path(out_dir).expanduser()
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OSError(
                f"dossier de destination inutilisable : {out} ({exc.strerror})"
            ) from exc

        def dest(kind: str) -> Path:
            d = out / batch.folder_for(kind) if by_type else out
            d.mkdir(parents=True, exist_ok=True)
            return d
        done, failed = [], []
        for uid in uids:
            p = self.pieces.get(uid)
            if p is None:
                failed.append({"ref": uid, "error": "piece inconnue"})
                continue
            # deux lots peuvent porter le meme repere : le nom de fichier doit
            # rester unique, donc on prefixe par le lot quand il y en a plusieurs
            if not p.in_scope:
                failed.append({"ref": p.ref,
                               "error": f"sans livrable : {p.verdict.detail}"})
                continue
            # Le nom porte la LFT d'origine et le repere : un fichier isole
            # dans un dossier de sous-traitance doit dire d'ou il vient.
            name = registry.output_basename(
                Path(self.source).stem if self.source else "", p.ref)

            # Forme non definie : une fiche de debit et rien d'autre. Elle part
            # dans debits/, pas dans plans/, pour qu'elle ne puisse pas etre
            # confondue avec un plan de fabrication.
            if p.cut_only:
                refuses = [f for f in formats if f in ("step", "stl", "brep", "dxf")]
                if refuses:
                    failed.append({
                        "ref": p.ref,
                        "error": f"forme non définie ({p.verdict.reason}) : "
                                 f"pas de {', '.join(refuses).upper()}, "
                                 "seule une fiche de débit est produite"})
                try:
                    data = self.debit_data(p)
                    if "pdf" in formats:
                        done.append(str(render.debit_sheet(
                            data, dest("debit") / f"{name}.pdf",
                            p.verdict.reason, p.verdict.detail)))
                    if "svg" in formats:
                        q = dest("svg") / f"{name}.svg"
                        q.write_text(render.debit_svg(
                            data, p.verdict.reason, p.verdict.detail),
                            encoding="utf-8")
                        done.append(str(q))
                    if "json" in formats:
                        q = dest("json") / f"{name}.json"
                        q.write_text(json.dumps(self._detail(p, 0.2),
                                                ensure_ascii=False, indent=2,
                                                default=str), encoding="utf-8")
                        done.append(str(q))
                except Exception as exc:
                    app_log.debug("fiche %s", p.ref, exc_info=True)
                    failed.append({"ref": p.ref,
                                   "error": f"{type(exc).__name__}: {exc}"})
                continue

            try:
                if p.tube is None:                               # pragma: no cover
                    raise ValueError("aucune géométrie pour cette pièce")
                cl = geometry.build(p.tube, handedness=self.config.handedness)
                blocked = validate.unsafe(p.issues)
                wanted_3d = [f for f in formats if f in ("step", "stl", "brep")]
                if blocked and wanted_3d:
                    # Un STEP faux part chez un sous-traitant sans que personne
                    # ne relise le plan. Le plan, lui, est ecrit : il porte le
                    # bandeau ERREUR qui dit exactement ce qui cloche.
                    failed.append({
                        "ref": p.ref,
                        "error": f"pas de modèle 3D : {blocked} — la géométrie "
                                 "n'est pas fiable, seul le plan est écrit"})
                    wanted_3d = []
                for fmt in wanted_3d:
                    for f in solid.export(p.tube, cl, dest(fmt), p.tooling,
                                          [fmt], basename=name):
                        done.append(str(f))
                if "pdf" in formats or "svg" in formats:
                    data = self.plan_data(p, cl)
                    if "pdf" in formats:
                        done.append(str(render.to_pdf(
                            data, dest("pdf") / f"{name}.pdf")))
                    if "svg" in formats:
                        q = dest("svg") / f"{name}.svg"
                        q.write_text(render.to_svg(data), encoding="utf-8")
                        done.append(str(q))
                if "dxf" in formats:
                    q = dest("dxf") / f"{name}.dxf"
                    render.to_dxf(p.tube, cl, str(q))
                    done.append(str(q))
                if "json" in formats:
                    q = dest("json") / f"{name}.json"
                    q.write_text(json.dumps(self._detail(p, 0.2), ensure_ascii=False,
                                            indent=2, default=str),
                                 encoding="utf-8")
                    done.append(str(q))
            except Exception as exc:
                # Le motif est ce qui compte : « 95 echecs » sans raison ne
                # permet pas de corriger quoi que ce soit.
                app_log.debug("export %s", p.ref, exc_info=True)
                failed.append({"ref": p.ref,
                               "error": f"{type(exc).__name__}: {exc}"})
        return {"written": done, "failed": failed, "dir": str(out),
                "reasons": _group_reasons(failed)}


def create_app(session: Session | None = None, token: str | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    # Flask trie les cles JSON par defaut, ce qui renverrait les outillages
    # dans l'ordre alphabetique : Ø10, Ø12, Ø15... avant Ø4. On conserve
    # l'ordre d'insertion, qui est l'ordre croissant des diametres.
    app.json.sort_keys = False
    app.config["SESSION"] = session or Session()
    app.config["TOKEN"] = token or secrets.token_urlsafe(16)
    app.config["CAMPAIGN"] = CampaignRunner()

    def api_error(exc: Exception, code: int = 400):
        app.logger.debug(traceback.format_exc())
        return jsonify({"error": str(exc), "type": type(exc).__name__}), code

    # ------------------------------------------------------------- statiques
    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/<path:filename>")
    def asset(filename: str):
        return send_from_directory(STATIC, filename)

    # -------------------------------------------------------------------- api
    @app.get("/api/diag")
    def diag():
        """Diagnostic d'environnement, utile sur un poste ou rien ne marche."""
        import platform
        mods = {}
        for name in ("cadquery", "OCP", "OCP.STEPControl", "OCP.BRepOffsetAPI",
                     "numpy", "openpyxl", "ezdxf", "flask"):
            try:
                __import__(name)
                mods[name] = "ok"
            except Exception as exc:
                mods[name] = f"{type(exc).__name__}: {exc}"
        return jsonify({
            "python": sys.version,
            "platform": platform.platform(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": sys.executable,
            "modules": mods,
        })

    @app.get("/api/status")
    def status():
        s: Session = app.config["SESSION"]
        return jsonify({
            "ready": True,
            "cad": _cad_available(),
            "cad_error": _cad_error(),
            "source": str(s.source) if s.source else None,
            "count": len(s.pieces),
            "diameters": sorted(bsa.RM),
        })

    @app.post("/api/upload")
    def upload():
        """Recoit un fichier choisi dans l'explorateur du systeme.

        Le navigateur ne communique jamais le chemin reel d'un fichier, pour
        des raisons de confidentialite. On recupere donc son contenu, on
        l'ecrit dans un dossier temporaire, et on traite ce fichier-la.
        """
        s: Session = app.config["SESSION"]
        f = request.files.get("file")
        if f is None or not f.filename:
            return api_error(ValueError("aucun fichier recu"))
        target = Path(s.workdir) / Path(f.filename).name
        f.save(target)
        try:
            if target.suffix.lower() in (".stp", ".step"):
                return jsonify({"kind": "step",
                                "data": stepreader.analyse(str(target))})
            return jsonify({"kind": "lft", "data": s.open_lft(str(target)),
                            "path": str(target)})
        except Exception as exc:
            return api_error(exc)

    @app.get("/api/settings")
    def get_settings():
        s: Session = app.config["SESSION"]
        return jsonify({
            "config": s.config.data,
            "conventions": sorted(conventions.REGISTRY),
            "angle_modes": list(bsa.ANGLE_MODES),
            "reference": {
                "rm": bsa.RM,
                "wall": bsa.WALL,
                "elongation": bsa.ELONGATION_PCT,
                "elasticity": bsa.ELASTICITY_PCT,
                "r15_for_90": bsa.R15_FOR_90,
                "min_straight": bsa.MIN_STRAIGHT,
                "min_last": bsa.MIN_LAST,
                "min_last_two": bsa.MIN_LAST_TWO,
                "max_last": bsa.MAX_LAST,
                "max_angle": bsa.MAX_BEND_ANGLE,
                "developed": [bsa.MIN_DEVELOPED, bsa.RECOMMENDED_DEVELOPED,
                              bsa.MAX_DEVELOPED],
            },
        })

    @app.post("/api/settings")
    def post_settings():
        """Applique une configuration et rejoue le fichier courant, pour que
        l'effet de chaque reglage soit immediatement visible."""
        s: Session = app.config["SESSION"]
        data = request.get_json(force=True, silent=True) or {}
        previous = s.config
        try:
            s.config = Config(data)
            conventions.get(s.config.convention)      # valide le nom
            return jsonify({"ok": True, "reloaded": s.rebuild()})
        except Exception as exc:
            s.config = previous
            s.invalidate()
            return api_error(exc)

    @app.post("/api/settings/reset")
    def reset_settings():
        s: Session = app.config["SESSION"]
        s.config = Config.load(None)
        return jsonify({"ok": True, "config": s.config.data,
                        "reloaded": s.rebuild()})

    @app.post("/api/open")
    def open_file():
        s: Session = app.config["SESSION"]
        data = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(s.open_lft(data.get("path", ""), data.get("sheet")))
        except Exception as exc:
            return api_error(exc)

    @app.get("/api/tubes")
    def tubes():
        return jsonify(app.config["SESSION"].summary())

    @app.get("/api/tube/<uid>")
    def tube_detail(uid: str):
        s: Session = app.config["SESSION"]
        try:
            defl = float(request.args.get("deflection", 0.04))
            return jsonify(s.detail(uid, defl))
        except KeyError as exc:
            return api_error(exc, 404)
        except Exception as exc:
            return api_error(exc)

    @app.post("/api/step")
    def read_step():
        data = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(stepreader.analyse(data.get("path", ""),
                                              float(data.get("deflection", 0.04))))
        except Exception as exc:
            return api_error(exc)

    @app.post("/api/export")
    def export():
        s: Session = app.config["SESSION"]
        data = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(s.export(data.get("uids") or list(s.pieces),
                                    data.get("dir") or ".",
                                    data.get("formats") or ["step"],
                                    by_type=bool(data.get("by_type", True))))
        except Exception as exc:
            return api_error(exc)

    # ------------------------------------------------------ selecteur natif
    @app.post("/api/pick")
    def pick():
        """Ouvre le selecteur du systeme et renvoie le chemin choisi."""
        data = request.get_json(force=True, silent=True) or {}
        mode = "--pick-file" if data.get("kind") == "file" else "--pick-folder"
        title = str(data.get("title") or "")
        initial = str(data.get("initial") or "")
        frozen = bool(getattr(sys, "frozen", False))
        cmd = [sys.executable] if frozen else [sys.executable, "-m", "tubeiso.app"]
        cmd += [mode]
        if title:
            cmd += ["--title", title]
        if initial:
            cmd += ["--initial", initial]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except Exception as exc:
            return api_error(RuntimeError(
                f"sélecteur de dossier indisponible : {exc}. "
                "Collez le chemin dans le champ."))
        if proc.returncode == 2:
            return api_error(RuntimeError(
                (proc.stderr or "sélecteur indisponible").strip()
                + " — collez le chemin dans le champ."))
        path = (proc.stdout or "").strip().splitlines()
        return jsonify({"path": path[-1] if path else "",
                        "cancelled": proc.returncode == 1})

    # ---------------------------------------------------------------- campagne
    @app.post("/api/batch/start")
    def batch_start():
        data = request.get_json(force=True, silent=True) or {}
        source = str(data.get("source") or "").strip()
        out_dir = str(data.get("output") or "").strip()
        if not source or not out_dir:
            return api_error(ValueError("dossier source et dossier de sortie requis"))
        formats = [f for f in (data.get("formats") or ["step"])
                   if f in ("step", "stl", "brep")] or ["step"]
        options = batch.Options(
            config=data.get("config") or None,
            repertoire=str(data.get("repertoire") or "").strip() or None,
            formats=tuple(formats),
            plans=bool(data.get("plans", True)),
            booklet=bool(data.get("booklet", True)),
            models=bool(data.get("models", True)),
            dxf=bool(data.get("dxf", False)),
            force=bool(data.get("force", False)),
            limit=int(data["limit"]) if data.get("limit") else None,
            workers=max(1, int(data.get("workers") or 1)),
        )
        try:
            return jsonify(app.config["CAMPAIGN"].start([source], out_dir, options))
        except Exception as exc:
            return api_error(exc)

    @app.get("/api/batch/status")
    def batch_status():
        return jsonify(app.config["CAMPAIGN"].state())

    @app.post("/api/batch/stop")
    def batch_stop():
        return jsonify(app.config["CAMPAIGN"].stop())

    @app.post("/api/shutdown")
    def shutdown():
        func = request.environ.get("werkzeug.server.shutdown")
        if func:
            func()
        return jsonify({"ok": True})

    return app


def _cad_available() -> bool:
    return not _cad_error()


def _cad_error() -> str:
    """Retourne le message d'erreur d'import du noyau CAO, ou une chaine vide."""
    try:
        import cadquery  # noqa: F401
        return ""
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
