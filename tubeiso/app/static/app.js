import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';
import { centerlineAt, stepLabel } from './simulation.js';

/* ══════════════════════════════════════════════════════ état de l'application */

const S = {
  lots: [],           // lots, chacun avec ses pièces
  uid: null,          // pièce sélectionnée (identifiant stable, pas le repère)
  detail: null,       // géométrie complète de la pièce affichée
  snaps: [],          // points d'accrochage pour la mesure
  measuring: false,
  picked: [],         // points cliqués en cours de mesure
  measures: [],       // mesures validées
  framePts: null,     // points servant au recadrage
  sim: null,          // données de simulation de la pièce affichée
  settings: null,     // configuration et valeurs de référence
  show: { mesh: true, wire: false, axis: true, nodes: true, dims: true, grid: true },
};

/* État de la simulation. Déclaré ici et non plus bas : la boucle de rendu
   démarre dès l'évaluation du module et y accède immédiatement. */
let simMesh = null;
let simPlaying = false;
let simProgress = 0;
let simLast = 0;

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const fmt = (v, n = 2) => Number(v).toFixed(n);
const esc = (s) => String(s ?? '').replace(/[<&>]/g,
  (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]));

function say(msg, kind = '') {
  const el = $('#status');
  el.textContent = msg;
  el.style.color = kind === 'err' ? 'var(--err)'
                 : kind === 'ok' ? 'var(--ok)' : '';
}
const busy = (on) => { $('#spinner').hidden = !on; };

/* Une erreur JavaScript silencieuse laisse l'interface figée sans explication.
   On les remonte dans la barre d'état et dans la console. */
window.addEventListener('error', (e) => {
  console.error(e.error || e.message);
  say(`Erreur interne : ${e.message}`, 'err');
});
window.addEventListener('unhandledrejection', (e) => {
  console.error(e.reason);
  say(`Erreur interne : ${e.reason?.message ?? e.reason}`, 'err');
});

async function api(path, opts) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({ error: 'réponse illisible' }));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

/* ═══════════════════════════════════════════════════════════════ scène 3D */

const canvas = $('#scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 20000);

/* La Crippa travaille en Z vertical : l'axe Z de la machine est le mouvement
   vertical de la tête, et la géométrie est construite avec le premier plan de
   cintrage dans (X, Z). On met donc le monde en Z-up, au lieu du Y-up par
   défaut de three.js. Sans cela le tube est couché sur le côté et le plateau
   le traverse au lieu de le porter. */
camera.up.set(0, 0, 1);
camera.position.set(220, -220, 170);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.09;

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const key = new THREE.DirectionalLight(0xffffff, 1.5);
key.position.set(1, -0.9, 1.4);
scene.add(key);
const fill = new THREE.DirectionalLight(0x9fc6ff, 0.5);
fill.position.set(-1, 0.8, -0.4);
scene.add(fill);

const model = new THREE.Group();     // contient la pièce, recentrée sur l'origine
scene.add(model);

/* Le plateau de référence et le trièdre sont reconstruits à chaque pièce :
   leur taille et leur position dépendent de l'encombrement. */
const stage = new THREE.Group();
scene.add(stage);

const MAT = {
  tube: new THREE.MeshStandardMaterial({
    color: 0xb9c4d2, metalness: 0.62, roughness: 0.34,
    side: THREE.DoubleSide, flatShading: false,
  }),
  wire: new THREE.MeshBasicMaterial({ color: 0x4aa8ff, wireframe: true,
                                      transparent: true, opacity: 0.35 }),
  axis: new THREE.LineBasicMaterial({ color: 0xffb54a, depthTest: false }),
  node: new THREE.MeshBasicMaterial({ color: 0x4aa8ff, depthTest: false }),
  vert: new THREE.MeshBasicMaterial({ color: 0xf2585b, depthTest: false }),
  pick: new THREE.MeshBasicMaterial({ color: 0x48c78e, depthTest: false }),
  meas: new THREE.LineDashedMaterial({ color: 0x48c78e, dashSize: 3,
                                       gapSize: 2, depthTest: false }),
  sim: new THREE.MeshStandardMaterial({ color: 0x8fb4d8, metalness: 0.5,
                                        roughness: 0.4, side: THREE.DoubleSide }),
};

let parts = {};   // sous-objets de `model`, pour les bascules d'affichage
let labels = [];  // { pos: Vector3, text, cls }

/** Libère la mémoire GPU d'une branche de la scène.
 *  three.js ne le fait pas tout seul : sans cela, chaque changement de pièce
 *  laissait un maillage complet sur la carte graphique. */
function disposeTree(root) {
  root.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    const m = o.material;
    if (Array.isArray(m)) m.forEach((x) => x.dispose && x.dispose());
  });
  root.clear();
}

function clearModel() {
  disposeTree(model);
  parts = {};
  labels = [];
  S.snaps = [];
  S.picked = [];
  S.measures = [];
  simMesh = null;
  clearLabelPool();
}

function vec(a) { return new THREE.Vector3(a[0], a[1], a[2]); }

/* ──────────────────────────────────────── plateau de référence et trièdre */

/** Pas de quadrillage « rond » couvrant la pièce : 1, 2, 5, 10, 20, 50… mm. */
function niceStep(span) {
  const target = span / 12;
  const pow = 10 ** Math.floor(Math.log10(Math.max(target, 1e-3)));
  const n = target / pow;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
}

/** Construit le plateau SOUS la pièce, à sa taille. Le plateau de la v4 était
 *  un carré fixe de 1000 mm placé en z = 0, donc il traversait la pièce. */
function buildStage(box) {
  disposeTree(stage);
  const size = box.getSize(new THREE.Vector3());
  const span = Math.max(size.x, size.y, 1);
  const step = niceStep(span);
  const half = Math.ceil((span * 0.85) / step) * step;
  const divisions = Math.max(2, Math.round((half * 2) / step));

  const grid = new THREE.GridHelper(half * 2, divisions, 0x3a4756, 0x232a34);
  grid.rotation.x = Math.PI / 2;             // GridHelper naît en XZ, on le met en XY
  grid.material.transparent = true;
  grid.material.opacity = 0.55;
  // le plateau porte la pièce : il se pose sous son point le plus bas
  grid.position.set(0, 0, -size.z / 2 - Math.max(span * 0.02, 1));
  stage.add(grid);

  // trièdre proportionné à la pièce, posé au coin du plateau
  const len = Math.max(span * 0.16, step);
  const axes = new THREE.AxesHelper(len);
  axes.position.set(-half, -half, grid.position.z);
  stage.add(axes);

  stage.userData = { step, z: grid.position.z, half, axisLength: len };
  stage.visible = S.show.grid;
}

/** Recentre la pièce sur l'origine, recadre la caméra, repose le plateau. */
function frame(points) {
  S.framePts = points;
  const box = new THREE.Box3();
  points.forEach((p) => box.expandByPoint(vec(p)));
  const c = box.getCenter(new THREE.Vector3());
  model.position.set(-c.x, -c.y, -c.z);

  const local = new THREE.Box3(
    box.min.clone().sub(c), box.max.clone().sub(c));
  buildStage(local);

  const size = box.getSize(new THREE.Vector3()).length() || 100;
  controls.target.set(0, 0, 0);
  const fov = THREE.MathUtils.degToRad(camera.fov);
  const d = (size / 2) / Math.tan(fov / 2) * 1.25;
  camera.position.set(d * 0.6, -d * 0.6, d * 0.45);
  camera.near = Math.max(size / 800, 0.01);
  camera.far = size * 60;
  camera.updateProjectionMatrix();
  controls.update();
}

/* ─────────────────────────────────────────── construction de la pièce affichée */

function buildMesh(mesh) {
  if (!mesh) return;
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(mesh.positions, 3));
  g.setAttribute('normal', new THREE.Float32BufferAttribute(mesh.normals, 3));
  g.setIndex(mesh.indices);
  parts.mesh = new THREE.Mesh(g, MAT.tube);
  parts.wire = new THREE.Mesh(g.clone(), MAT.wire);
  model.add(parts.mesh, parts.wire);
}

function buildAxis(polyline) {
  const g = new THREE.BufferGeometry().setFromPoints(polyline.map(vec));
  parts.axis = new THREE.Line(g, MAT.axis);
  parts.axis.renderOrder = 5;
  model.add(parts.axis);
}

function buildNodes(detail, scale) {
  const grp = new THREE.Group();
  const rT = Math.max(scale * 0.012, 0.6);
  const sphere = new THREE.SphereGeometry(rT, 16, 12);

  const add = (pts, mat, kind) => (pts || []).forEach((p, i) => {
    const m = new THREE.Mesh(sphere, mat);
    m.renderOrder = 6;
    m.position.copy(vec(p));
    m.userData = { point: p, kind, index: i };
    grp.add(m);
    S.snaps.push(m);
  });
  add(detail.tangents, MAT.node, 'tangence');
  add(detail.vertices, MAT.vert, 'sommet');

  parts.nodes = grp;
  model.add(grp);
}

function buildLabels(detail) {
  labels = [];
  let bend = 0;
  for (const p of detail.primitives) {
    if (p.kind === 'line') {
      const mid = [(p.start[0] + p.end[0]) / 2, (p.start[1] + p.end[1]) / 2,
                   (p.start[2] + p.end[2]) / 2];
      labels.push({ pos: vec(mid), text: fmt(p.length, 1), cls: 'len' });
    } else {
      const b = detail.bends[bend];
      // On affiche l'angle RÉEL, celui du tube fini. Le R15 programmé est
      // rappelé entre parenthèses quand il en diffère : c'est la donnée que
      // l'opérateur retrouve dans le programme.
      let txt = `${fmt(p.angle, 1)}°`;
      if (b && b.springback) txt += ` (R15 ${b.r15})`;
      if (b && b.rotation) txt += `  ↻${fmt(b.rotation, 0)}°`;
      labels.push({ pos: vec(p.mid), text: txt, cls: 'ang' });
      bend += 1;
    }
  }
}

function showTube(detail) {
  clearModel();
  S.detail = detail;
  const scale = Math.max(...detail.bbox.size, 1);
  buildMesh(detail.mesh);
  buildAxis(detail.polyline);
  buildNodes(detail, scale);
  buildLabels(detail);
  applyToggles();
  frame(detail.polyline);
  $('#hint').style.display = 'none';
  if (detail.mesh_error) say(`Solide non généré : ${detail.mesh_error}`, 'err');
}

/** Affiche un STEP lu depuis le disque (pas issu d'un programme). */
function showStep(res) {
  clearModel();
  S.detail = null;
  S.sim = null;
  const pts = res.lra.vertices.length ? res.lra.vertices
            : [res.bbox.min, res.bbox.max];
  const scale = Math.max(...res.bbox.size, 1);
  buildMesh(res.mesh);
  if (res.lra.vertices.length > 1) buildAxis(res.lra.vertices);
  buildNodes({ tangents: [], vertices: res.lra.vertices }, scale);

  labels = res.features.map((f) => ({
    pos: vec([(f.start[0] + f.end[0]) / 2, (f.start[1] + f.end[1]) / 2,
              (f.start[2] + f.end[2]) / 2]),
    cls: f.kind === 'line' ? 'len' : 'ang',
    text: f.kind === 'line' ? fmt(f.length, 2) : `${fmt(f.angle, 2)}°`,
  }));
  applyToggles();
  frame(pts);
  $('#hint').style.display = 'none';
}

/* ────────────────────────────────────────────────────────────── bascules 3D */

function applyToggles() {
  if (parts.mesh) parts.mesh.visible = S.show.mesh && !simMesh;
  if (parts.wire) parts.wire.visible = S.show.wire && !simMesh;
  if (parts.axis) parts.axis.visible = S.show.axis && !simMesh;
  if (parts.nodes) parts.nodes.visible = S.show.nodes && !simMesh;
  stage.visible = S.show.grid;
  $('#labels').style.display = (S.show.dims && !simMesh) ? '' : 'none';
}

/* ──────────────────────────────────────────────────── étiquettes projetées */

/* Le pool d'étiquettes DOIT être vidé en même temps que le conteneur.
   En v4 il ne l'était pas : après un changement de pièce, les <div> du pool
   étaient détachés du DOM et plus rien ne s'affichait — c'est l'origine des
   « cotes qui marchent une fois sur deux ». */
let labelPool = [];

function clearLabelPool() {
  labelPool = [];
  const host = $('#labels');
  while (host.firstChild) host.removeChild(host.firstChild);
}

function ensurePool(n) {
  const host = $('#labels');
  while (labelPool.length < n) {
    const d = document.createElement('div');
    d.className = 'lab';
    host.appendChild(d);
    labelPool.push(d);
  }
  for (let i = n; i < labelPool.length; i += 1) labelPool[i].hidden = true;
}

const _p = new THREE.Vector3();
function drawLabels() {
  const all = [...labels, ...S.measures.map((m) => ({
    pos: m.mid, text: `${fmt(m.dist, 2)} mm`, cls: 'mes',
  }))];
  ensurePool(all.length);

  const w = canvas.clientWidth, h = canvas.clientHeight;
  const placed = [];
  all.forEach((l, i) => {
    const el = labelPool[i];
    _p.copy(l.pos).add(model.position).project(camera);
    if (_p.z <= -1 || _p.z >= 1) { el.hidden = true; return; }
    el.hidden = false;
    el.className = `lab ${l.cls}`;
    el.textContent = l.text;

    const x = (_p.x * 0.5 + 0.5) * w;
    let y = (-_p.y * 0.5 + 0.5) * h;
    // anti-chevauchement : on redescend tant qu'une étiquette déjà posée gêne,
    // et on re-teste depuis le début après chaque décalage
    for (let pass = 0; pass < 12; pass += 1) {
      const hit = placed.find((o) => Math.abs(o.x - x) < 76 && Math.abs(o.y - y) < 18);
      if (!hit) break;
      y = hit.y + 19;
    }
    placed.push({ x, y });
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
  });
}

/* ───────────────────────────────────────────────────────── mesure au clic */

const ray = new THREE.Raycaster();
const ptr = new THREE.Vector2();

canvas.addEventListener('pointerdown', (e) => {
  if (!S.measuring || !S.snaps.length) return;
  const r = canvas.getBoundingClientRect();
  ptr.x = ((e.clientX - r.left) / r.width) * 2 - 1;
  ptr.y = -((e.clientY - r.top) / r.height) * 2 + 1;
  ray.setFromCamera(ptr, camera);
  const hit = ray.intersectObjects(S.snaps, false)[0];
  if (!hit) return;

  const o = hit.object;
  o.material = MAT.pick;
  S.picked.push(o);
  if (S.picked.length === 2) {
    const [a, b] = S.picked.map((m) => vec(m.userData.point));
    const dist = a.distanceTo(b);
    const g = new THREE.BufferGeometry().setFromPoints([a, b]);
    const line = new THREE.Line(g, MAT.meas);
    line.computeLineDistances();
    model.add(line);
    S.measures.push({ mid: a.clone().lerp(b, 0.5), dist, line });
    S.picked.forEach((m) => {
      m.material = m.userData.kind === 'sommet' ? MAT.vert : MAT.node;
    });
    S.picked = [];
    say(`Distance exacte : ${fmt(dist, 3)} mm`, 'ok');
  } else {
    say(`Point ${o.userData.kind} — cliquez le second point.`);
  }
});

$('#measure').onclick = (e) => {
  S.measuring = !S.measuring;
  e.target.classList.toggle('on', S.measuring);
  canvas.style.cursor = S.measuring ? 'crosshair' : '';
  say(S.measuring ? 'Mode mesure : cliquez deux points d\'accrochage.' : 'Prêt.');
};

$('#clearMeasure').onclick = () => {
  S.measures.forEach((m) => { model.remove(m.line); m.line.geometry.dispose(); });
  S.measures = [];
  S.picked.forEach((m) => {
    m.material = m.userData.kind === 'sommet' ? MAT.vert : MAT.node;
  });
  S.picked = [];
  say('Mesures effacées.');
};

/* ──────────────────────────────────────────────────────────── boucle rendu */

function resize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe($('#canvas-wrap'));

function loop(now) {
  requestAnimationFrame(loop);
  controls.update();
  tickSim(now || performance.now());
  renderer.render(scene, camera);
  if (S.show.dims && !simMesh) drawLabels();
  $('#readout').textContent = S.detail
    ? `Ø${S.detail.diameter} · ${S.detail.bends.length} coude(s) · `
      + `pièce ${fmt(S.detail.developed, 1)} mm`
      + (S.detail.declared ? ` · brut R6 ${S.detail.declared} mm` : '')
      + (stage.userData.step ? ` · quadrillage ${stage.userData.step} mm` : '')
    : '';
}
resize();
loop();

/* ═══════════════════════════════════════════════════════════ panneau droit */

function renderDims(d) {
  const rows = [];
  let bend = 0;
  d.straights.forEach((s, i) => {
    const isLast = i === d.straights.length - 1;
    const label = i === 0 ? 'R12 (départ)'
                : isLast ? 'Dernier segment' : `Segment ${i + 1}`;
    rows.push(`<tr><td>${label}</td><td class="num">${fmt(s, 2)}</td>
               <td class="num">—</td><td class="num">—</td></tr>`);
    if (!isLast && d.bends[bend]) {
      const b = d.bends[bend];
      const prog = b.springback
        ? `<span class="prog" title="angle programmé, élasticité comprise">R15 ${b.r15}°</span>`
        : '';
      rows.push(`<tr><td>Coude ${bend + 1} ${prog}</td><td class="num">—</td>
                 <td class="num">${fmt(b.angle, 1)}°</td>
                 <td class="num">${fmt(b.rotation, 1)}°</td></tr>`);
      bend += 1;
    }
  });

  const m = d.matiere || {};
  const spring = d.bends.reduce((a, b) => a + (b.springback || 0), 0);
  const modes = { entier: 'arrondi entier', proportionnel: 'proportionnel',
                  brut: 'aucune (R15 brut)' };

  $('#tab-dims').innerHTML = `
    <h3 class="sec">Identification</h3>
    <dl class="kv">
      <dt>Repère</dt><dd>${esc(d.ref)}</dd>
      <dt title="Numéro du programme">Programme</dt><dd>${esc(d.programme) || '—'}</dd>
      <dt title="Numéro de LFT, c'est-à-dire le lot">Liste / lot</dt>
        <dd>${esc(d.liste) || '—'}</dd>
      <dt>Diamètre</dt><dd>Ø${d.diameter}${d.wall ? ` × ${d.wall}` : ''}</dd>
      <dt>Outillage</dt><dd>${esc(d.tooling) || '—'} · ${esc(d.head)}</dd>
      ${d.bends.length ? `<dt>Rayon Rm</dt><dd>${d.bend_radius ?? '—'} mm</dd>`
                       : '<dt>Opération</dt><dd>débit droit, sans cintrage</dd>'}
    </dl>

    <h3 class="sec">Matière</h3>
    <dl class="kv">
      <dt>Nature</dt><dd><span class="nat ${m.nature === 'souple' ? 'souple'
          : m.nature === 'rigide' ? 'rigide' : 'unk'}">${esc(m.nature || 'inconnue')}</span>
        ${m.cintrable ? ' · cintrable Crippa' : ' · non cintrable'}</dd>
      <dt>Désignation</dt><dd>${esc(m.matiere || d.material || '—')}</dd>
      <dt>Code BSA</dt><dd>${esc(m.code_matiere) || '—'}</dd>
      <dt>Famille</dt><dd>${esc(m.famille) || '—'}</dd>
      <dt>Épaisseur paroi</dt><dd>${m.paroi != null ? m.paroi + ' mm' : '—'}</dd>
      <dt>Périmètre</dt><dd>${m.statut === 'exclue'
          ? `<b class="bad">exclue</b> — ${esc(m.motif)} : ${esc(m.detail)}`
          : '<b class="good">traitée</b> — plan et 3D générés'}</dd>
    </dl>

    <h3 class="sec">Cotations du tube fini</h3>
    <table>
      <thead><tr><th>Élément</th><th class="num">L</th>
        <th class="num">Angle réel</th><th class="num">Rot.</th></tr></thead>
      <tbody>${rows.join('')}
        <tr class="tot"><td>Développé pièce</td>
          <td class="num">${fmt(d.developed, 2)}</td><td></td><td></td></tr>
      </tbody>
    </table>

    ${d.bends.length ? `<h3 class="sec">Retour élastique</h3>
    <dl class="kv">
      <dt>Correction</dt><dd>${modes[d.angle_mode] || d.angle_mode}</dd>
      <dt>Total retiré</dt><dd>${spring ? `${fmt(spring, 1)}°` : 'aucun'}</dd>
    </dl>
    <p class="note">Les angles ci-dessus sont ceux du tube <em>après</em>
      pliage. Le programme écrit ${spring ? 'des valeurs plus grandes' : 'les mêmes valeurs'}
      pour compenser l'élasticité du tube <span class="src">[DOC 5.4]</span>.</p>` : ''}

    <h3 class="sec">Contrôle</h3>
    <dl class="kv">
      <dt title="Longueur du brut à débiter">R6 déclaré</dt>
        <dd>${d.declared ?? '—'} mm</dd>
      <dt title="Dernier segment annoncé dans le commentaire">DS annoncé</dt>
        <dd>${d.ds ?? '—'} mm</dd>
      <dt>Recoupe</dt><dd>${d.recut || 0} mm</dd>
      <dt>Encombrement</dt><dd>${d.bbox.size.map((v) => fmt(v, 1)).join(' × ')}</dd>
    </dl>
    <p class="note">Valeurs analytiques, issues des primitives exactes — jamais
      mesurées sur le maillage d'affichage.</p>`;
}

function renderStepDims(res) {
  const l = res.lra;
  const rows = [];
  for (let i = 0; i < l.segments.length; i += 1) {
    rows.push(`<tr><td>Segment ${i + 1}</td><td class="num">${fmt(l.segments[i], 3)}</td>
               <td class="num">—</td><td class="num">—</td></tr>`);
    if (i < l.angles.length) {
      rows.push(`<tr><td>Coude ${i + 1}</td><td class="num">—</td>
                 <td class="num">${fmt(l.angles[i], 2)}°</td>
                 <td class="num">${fmt(l.rotations[i] ?? 0, 2)}°</td></tr>`);
    }
  }
  $('#tab-dims').innerHTML = `
    <h3 class="sec">Fichier STEP</h3>
    <dl class="kv">
      <dt>Fichier</dt><dd>${esc(res.file)}</dd>
      <dt>Diamètre lu</dt><dd>${l.tube_radius ? `Ø${fmt(l.tube_radius * 2, 3)}` : '—'}</dd>
      <dt>Paroi</dt><dd>${l.wall ? `${fmt(l.wall, 3)} mm` : '—'}</dd>
      <dt>Rayons Rm</dt><dd>${[...new Set(l.bend_radii)].map((v) => fmt(v, 2)).join(', ') || '—'}</dd>
      <dt>Volume</dt><dd>${res.volume ? `${fmt(res.volume, 1)} mm³` : '—'}</dd>
      <dt>Encombrement</dt><dd>${res.bbox.size.map((v) => fmt(v, 2)).join(' × ')}</dd>
    </dl>
    <h3 class="sec">Cotations extraites (exactes)</h3>
    <table>
      <thead><tr><th>Élément</th><th class="num">L</th>
        <th class="num">Angle</th><th class="num">Rot.</th></tr></thead>
      <tbody>${rows.join('')}
        <tr class="tot"><td>Développé</td>
          <td class="num">${fmt(l.developed, 3)}</td><td></td><td></td></tr>
      </tbody>
    </table>
    <p class="note">Lues dans les surfaces exactes du B-Rep (cylindres et
      tores), pas mesurées sur le maillage.</p>`;

  $('#tab-diag').innerHTML = res.warnings.length
    ? res.warnings.map((w) => `<div class="issue alerte">${esc(w)}</div>`).join('')
    : '<div class="issue info">Aucune anomalie détectée à la lecture.</div>';
  $('#tab-prog').innerHTML = '<p class="empty">Fichier STEP : pas de programme source.</p>';
  $('#tab-lft').innerHTML = '<p class="empty">Fichier STEP : pas de ligne LFT.</p>';
}

function renderDiag(d) {
  $('#tab-diag').innerHTML = d.issues.length
    ? d.issues.map((i) => `<div class="issue ${i.level}">
         <div><strong>${esc(i.code)}</strong><br>${esc(i.message)}
         ${i.source ? `<code>source : ${esc(i.source)}</code>` : ''}</div></div>`).join('')
    : '<div class="issue info">Aucune anomalie. La pièce est conforme.</div>';
}

/** Toutes les colonnes de la LFT, sans exception : la garantie qu'aucune
 *  information du fichier n'est perdue en route. */
function renderLft(d) {
  const fields = d.fields || [];
  if (!fields.length) {
    $('#tab-lft').innerHTML = '<p class="empty">Aucune donnée LFT.</p>';
    return;
  }
  const rows = fields.map((f) => `
    <tr class="${f.conflict ? 'conflict' : ''}">
      <td class="col">${esc(f.column)}</td>
      <td>${f.values.map(esc).join('<br>')}
        ${f.conflict ? `<em class="warnmark">${f.values.length} valeurs, lignes ${f.rows.join(', ')}</em>` : ''}</td>
    </tr>`).join('');
  $('#tab-lft').innerHTML = `
    <h3 class="sec">Ligne(s) LFT — ${fields.length} colonnes renseignées</h3>
    <table class="lft"><tbody>${rows}</tbody></table>
    <p class="note">Toutes les colonnes non vides du fichier sont conservées,
      y compris celles que l'application n'exploite pas. Une ligne orange porte
      plusieurs valeurs différentes pour la même colonne.</p>`;
}

/* ═══════════════════════════════════════════════════════════ panneau gauche */

function renderList() {
  const q = $('#filter').value.trim().toLowerCase();
  const host = $('#list');
  let total = 0, shown = 0;
  const html = [];

  for (const lot of S.lots) {
    const items = lot.tubes.filter((t) => !q
      || t.ref.toLowerCase().includes(q)
      || (t.programme || '').toLowerCase().includes(q)
      || (t.lot || '').toLowerCase().includes(q));
    total += lot.tubes.length;
    if (!items.length) continue;
    shown += items.length;
    // Chaque lot est encadré et porte son numéro : la séparation entre lots
    // doit se voir immédiatement.
    html.push(`<section class="lot">
      <header class="lot-head"><span class="lot-no">${esc(lot.label)}</span>
        <span class="chip">${items.length}</span></header>
      <ul>${items.map((t) => {
        const out = t.scope === 'exclue';
        // Une pièce hors périmètre ne produit ni plan ni 3D : elle reste
        // visible, mais son motif doit se lire sans clic.
        const dot = out ? 'out'
                  : t.status === 'erreur' ? 'err'
                  : t.status === 'alerte' ? 'warn' : 'info';
        // « 0c » pour un tube droit n'apprend rien : on nomme ce qu'il est.
        const meta = out ? esc(t.reason || 'hors périmètre')
                  : t.bends ? `Ø${t.diameter} · ${t.bends}c${t.rows > 1 ? ` · ${t.rows}L` : ''}`
                            : `Ø${t.diameter} · droit`;
        return `
        <li data-uid="${t.uid}" class="${t.uid === S.uid ? 'on' : ''}${out ? ' out' : ''}"
            title="${esc(t.matiere || '')}${t.reason_label ? ' — ' + esc(t.reason_label) : ''}">
          <i class="dot ${dot}"></i>
          <span class="ref">${esc(t.ref)}</span>
          <span class="nat ${t.nature === 'souple' ? 'souple' : t.nature === 'rigide' ? 'rigide' : 'unk'}"
                >${t.nature === 'souple' ? 'souple' : t.nature === 'rigide' ? 'rigide' : '?'}</span>
          <span class="meta">${meta}</span>
        </li>`;
      }).join('')}</ul>
    </section>`);
  }

  $('#count').textContent = q ? `${shown}/${total}` : String(total);
  host.innerHTML = html.join('') || '<p class="empty">Aucune pièce.</p>';
  $$('#list li').forEach((li) => { li.onclick = () => select(li.dataset.uid); });
}

function findTube(uid) {
  for (const lot of S.lots) {
    const t = lot.tubes.find((x) => x.uid === uid);
    if (t) return t;
  }
  return null;
}

async function select(uid) {
  S.uid = uid;
  renderList();
  busy(true);
  const t = findTube(uid);
  say(`Chargement du repère ${t ? t.ref : uid}…`);
  try {
    const d = await api(`/api/tube/${encodeURIComponent(uid)}`);
    S.sim = d.simulation && d.simulation.bends.length ? d.simulation : null;
    stopSim();

    if (d.out_of_scope) {
      // Aucune géométrie n'existe pour cette pièce, et il ne faut surtout pas
      // en inventer une : la vue se vide et le panneau explique pourquoi.
      clearModel();
      S.detail = null;
      renderOutOfScope(d);
      say(`${d.ref} — hors périmètre : ${d.reason_label || d.reason}`, 'err');
      return;
    }

    showTube(d);
    renderDims(d);
    renderDiag(d);
    renderLft(d);
    $('#tab-prog').innerHTML = d.source
      ? `<pre class="prog">${esc(d.source)}</pre>`
      : '<p class="empty">Tube droit : aucun programme de cintrage.</p>';
    say(`${d.ref} · programme ${d.programme || '—'} · lot ${d.liste || '—'} — `
        + (d.status === 'erreur' ? 'erreurs détectées'
         : d.status === 'alerte' ? 'alertes' : 'conforme'),
        d.status === 'erreur' ? 'err' : d.status === 'info' ? 'ok' : '');
  } catch (e) {
    say(e.message, 'err');
  } finally {
    busy(false);
  }
}


/* ───────────────────────────────────── pièces hors périmètre

   Une pièce écartée n'a pas de géométrie. Lui en fabriquer une « pour avoir
   quelque chose à montrer » est exactement ce qu'il ne faut pas faire : un
   tuyau souple de 5 m s'affichait en 3D avec un développé, ce qu'on pouvait
   prendre pour une pièce réelle. */
function renderOutOfScope(d) {
  const m = d.matiere || {};
  $('#hint').style.display = '';
  $('#hint').innerHTML = `<b>${esc(d.ref)} — hors périmètre</b><br>`
    + `${esc(d.reason_label || d.reason || '')}<br>`
    + `<span class="muted">Aucun modèle n'est calculé pour cette pièce.</span>`;

  $('#tab-dims').innerHTML = `
    <h3 class="sec">Identification</h3>
    <dl class="kv">
      <dt>Repère</dt><dd>${esc(d.ref)}</dd>
      <dt>Programme</dt><dd>${esc(d.programme) || '—'}</dd>
      <dt>Liste / lot</dt><dd>${esc(d.liste) || '—'}</dd>
      <dt>Longueur LFT</dt><dd>${d.declared != null ? fmt(d.declared, 0) + ' mm' : '—'}</dd>
    </dl>
    <h3 class="sec">Matière</h3>
    <dl class="kv">
      <dt>Nature</dt><dd><span class="nat ${m.nature === 'souple' ? 'souple'
          : m.nature === 'rigide' ? 'rigide' : 'unk'}">${esc(m.nature || 'inconnue')}</span></dd>
      <dt>Désignation</dt><dd>${esc(m.matiere || '—')}</dd>
      <dt>Code BSA</dt><dd>${esc(m.code_matiere) || '—'}</dd>
      <dt>Famille</dt><dd>${esc(m.famille) || '—'}</dd>
    </dl>
    <h3 class="sec">Pourquoi cette pièce est écartée</h3>
    <div class="issue alerte"><div><b>${esc(d.reason || 'hors périmètre')}</b>
      ${esc(d.reason_label || '')}</div></div>
    <p class="note">Ni plan ni modèle 3D ne sont produits. La pièce reste
      listée, ici comme dans <em>INDEX.xlsx</em>, avec ce motif : une campagne
      rend compte de toutes les lignes lues, y compris celles qu'elle n'a pas
      traitées.</p>`;

  renderDiag(d);
  renderLft(d);
  $('#tab-prog').innerHTML = d.source
    ? `<pre class="prog">${esc(d.source)}</pre>`
    : '<p class="empty">Aucune PROGCRIPPA sur cette ligne.</p>';
}

/* ══════════════════════════════════════════════════════════════════ actions */

function applyLoaded(data) {
  S.lots = data.lots || [];
  S.uid = null;
  renderList();
  const all = S.lots.flatMap((l) => l.tubes);
  const inScope = all.filter((t) => t.scope !== 'exclue');
  const ok = inScope.filter((t) => t.status !== 'erreur').length;
  const lots = S.lots.length;
  say(`${data.count} pièce(s) dans ${lots} lot(s) — ${inScope.length} dans le périmètre `
      + `PROGCRIPPA, ${all.length - inScope.length} exclue(s) — ${ok} exploitable(s), `
      + `${all.length - ok} à corriger.`);
  (data.warnings || []).forEach((w) => console.warn('LFT :', w));
  if (all.length) select(all[0].uid);
}

$('#open').onclick = async () => {
  const path = $('#path').value.trim();
  if (!path) { say('Indiquez un chemin de fichier.', 'err'); return; }
  busy(true);
  try {
    if (/\.(stp|step)$/i.test(path)) {
      const res = await api('/api/step', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      S.lots = []; S.uid = null; renderList();
      showStep(res); renderStepDims(res);
      say(`STEP lu : ${res.features.length} éléments reconnus.`, 'ok');
    } else {
      applyLoaded(await api('/api/open', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      }));
    }
  } catch (e) {
    say(e.message, 'err');
  } finally {
    busy(false);
  }
};

$('#path').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#open').click(); });

/* Le navigateur ne divulgue jamais le chemin réel d'un fichier. On envoie
   donc son contenu au serveur local, qui l'écrit dans un dossier temporaire
   et le traite. Fonctionne en fenêtre native comme en onglet. */
$('#browse').onclick = () => $('#file').click();

$('#file').onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const form = new FormData();
  form.append('file', f);
  busy(true);
  say(`Lecture de ${f.name}…`);
  try {
    const res = await api('/api/upload', { method: 'POST', body: form });
    $('#path').value = f.name;
    if (res.kind === 'step') {
      S.lots = []; S.uid = null; renderList();
      showStep(res.data); renderStepDims(res.data);
      say(`STEP lu : ${res.data.features.length} éléments reconnus.`, 'ok');
    } else {
      applyLoaded(res.data);
    }
  } catch (err) {
    say(err.message, 'err');
  } finally {
    busy(false);
    e.target.value = '';
  }
};
$('#filter').oninput = renderList;

$$('[data-toggle]').forEach((b) => {
  b.onclick = () => {
    const k = b.dataset.toggle;
    S.show[k] = !S.show[k];
    b.classList.toggle('on', S.show[k]);
    applyToggles();
  };
});

/* Vues : le monde est en Z-up, les directions le sont donc aussi. */
$$('[data-view]').forEach((b) => {
  b.onclick = () => {
    const d = camera.position.length();
    const v = { iso: [0.62, -0.62, 0.48], x: [1, 0, 0.001],
                y: [0, 1, 0.001], z: [0, 0.001, 1] }[b.dataset.view];
    camera.position.set(v[0], v[1], v[2]).normalize().multiplyScalar(d);
    controls.update();
  };
});

$('#fit').onclick = () => { if (S.framePts) frame(S.framePts); };

$$('.tabs button').forEach((b) => {
  b.onclick = () => {
    $$('.tabs button').forEach((x) => x.classList.remove('on'));
    $$('.tab').forEach((x) => x.classList.remove('on'));
    b.classList.add('on');
    $(`#tab-${b.dataset.tab}`).classList.add('on');
    if (b.dataset.tab === 'set') renderSettings();
  };
});

/* ------------------------------------------------------------------ exports */

let exportScope = 'one';
$('#exportOne').onclick = () => { exportScope = 'one'; $('#exportDlg').showModal(); };
$('#exportAll').onclick = () => { exportScope = 'all'; $('#exportDlg').showModal(); };

$('#doExport').onclick = async (e) => {
  e.preventDefault();
  const formats = $$('#exportDlg fieldset input[type=checkbox]:checked')
    .map((i) => i.value).filter(Boolean);
  const dir = $('#outDir').value.trim() || '.';
  const uids = exportScope === 'all'
    ? S.lots.flatMap((l) => l.tubes.map((t) => t.uid))
    : (S.uid ? [S.uid] : []);
  $('#exportDlg').close();
  if (!uids.length) { say('Aucune pièce sélectionnée.', 'err'); return; }
  if (!formats.length) { say('Choisissez au moins un format.', 'err'); return; }
  busy(true);
  try {
    const res = await api('/api/export', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uids, dir, formats, by_type: $('#byType').checked }),
    });
    const msg = `${res.written.length} fichier(s) écrit(s) dans ${res.dir}`;
    if (!res.failed.length) { say(msg, 'ok'); return; }
    // Une liste de repères ne dit pas quoi corriger : on affiche la cause,
    // regroupée, et le détail complet part dans la console.
    const top = (res.reasons || [])[0];
    const cause = top ? ` — ${top.count} échec(s) : ${top.error}` : '';
    say(`${msg}${cause}`, 'err');
    console.warn('Échecs d\'export :', res.reasons || res.failed);
  } catch (err) {
    say(err.message, 'err');
  } finally {
    busy(false);
  }
};


/* ─────────────────────────────────────────── sélecteur de dossier natif

   Le navigateur ne donne jamais le chemin réel d'un dossier. Le serveur
   tourne en local : il ouvre donc la fenêtre du système et nous rend le
   chemin choisi. Si Tk manque sur le poste, on le dit et le champ texte
   reste utilisable. */
async function pickPath(kind, title, initial) {
  const r = await api('/api/pick', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, title, initial }),
  });
  return r.cancelled ? null : (r.path || null);
}

document.addEventListener('click', async (ev) => {
  const btn = ev.target.closest('button.pick');
  if (!btn) return;
  ev.preventDefault();
  const field = $(`#${btn.dataset.target}`);
  btn.disabled = true;
  try {
    const p = await pickPath(btn.dataset.kind || 'folder',
                             btn.dataset.title || 'Choisir', field.value.trim());
    if (p) field.value = p;
  } catch (err) {
    say(err.message, 'err');
  } finally {
    btn.disabled = false;
  }
});

/* ──────────────────────────────────────────────────────────── campagne */

let batchTimer = null;

$('#openBatch').onclick = () => {
  $('#batchDlg').showModal();
  refreshBatch();
};

$('#batchStart').onclick = async (e) => {
  e.preventDefault();
  const body = {
    source: $('#batchSource').value.trim(),
    output: $('#batchOut').value.trim(),
    repertoire: $('#batchReg').value.trim(),
    plans: $('#bPlans').checked,
    booklet: $('#bBooklet').checked,
    models: $('#bModels').checked,
    dxf: $('#bDxf').checked,
    force: $('#bForce').checked,
    formats: ['step'].concat($('#bStl').checked ? ['stl'] : []),
    limit: Number($('#bLimit').value) || null,
    workers: Number($('#bWorkers').value) || 1,
  };
  if (!body.source || !body.output) {
    say('Indiquez le dossier des LFT et le dossier de sortie.', 'err');
    return;
  }
  try {
    await api('/api/batch/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    startBatchPolling();
  } catch (err) {
    say(err.message, 'err');
  }
};

$('#batchStop').onclick = async (e) => {
  e.preventDefault();
  try { await api('/api/batch/stop', { method: 'POST' }); } catch (err) { /* rien */ }
};

function startBatchPolling() {
  if (batchTimer) clearInterval(batchTimer);
  batchTimer = setInterval(refreshBatch, 900);
  refreshBatch();
}

async function refreshBatch() {
  let st;
  try { st = await api('/api/batch/status'); } catch (err) { return; }
  const box = $('#batchProgress');
  const started = st.total > 0;
  box.hidden = !started;
  $('#batchStart').disabled = st.running;
  $('#batchStop').hidden = !st.running;

  const pct = st.total ? Math.round((st.done / st.total) * 100) : 0;
  $('#pbarFill').style.width = `${pct}%`;
  const sum = st.summary || {};
  const bilan = sum.pieces != null
    ? ` — ${sum.pieces} pièce(s) : ${sum.traitees} cintrée(s), `
      + `${sum.tubes_droits || 0} droite(s), ${sum.exclues} hors périmètre · `
      + `${sum.plans} plan(s), ${sum.modeles_3d} modèle(s)`
    : '';
  $('#batchState').textContent = st.error
    ? `Échec : ${st.error}`
    : `${st.done}/${st.total} fichier(s)${st.running ? ` — ${st.current}` : ''}`
      + `${st.cancelling && st.running ? ' — arrêt demandé' : ''}${bilan}`
      + (!st.running && st.index ? ` · index : ${st.index}` : '');
  $('#batchLog').textContent = (st.lines || []).join('\n');
  $('#batchLog').scrollTop = $('#batchLog').scrollHeight;

  if (!st.running && batchTimer) { clearInterval(batchTimer); batchTimer = null; }
}

/* ───────────────────────────────────────────────────────────── démarrage */

api('/api/status').then((s) => {
  $('#cad').textContent = s.cad ? 'Noyau CAO actif' : 'Noyau CAO absent — 3D indisponible';
  $('#cad').style.color = s.cad ? '' : 'var(--warn)';
  const qs = new URLSearchParams(location.search);
  const wanted = qs.get('tab');
  if (wanted) {
    const b = document.querySelector(`.tabs button[data-tab="${wanted}"]`);
    if (b) b.click();
  }
  const start = qs.get('path') || s.source;
  if (start) { $('#path').value = start; $('#open').click(); }
}).catch(() => say('Serveur injoignable.', 'err'));

/* ═══════════════════════════════════════════ simulation du cintrage */

function drawSim() {
  if (!S.sim || !S.detail) return;
  const pts = centerlineAt(S.sim, simProgress);
  const curve = new THREE.CatmullRomCurve3(pts, false, 'catmullrom', 0.02);
  const geom = new THREE.TubeGeometry(
    curve, Math.min(420, pts.length * 3), (S.detail.diameter || 6) / 2, 14, false,
  );
  if (simMesh) {
    simMesh.geometry.dispose();
    simMesh.geometry = geom;
  } else {
    simMesh = new THREE.Mesh(geom, MAT.sim);
    model.add(simMesh);
  }
  $('#simRange').value = String(Math.round(
    (simProgress / Math.max(S.sim.bends.length, 1)) * 1000));

  const st = stepLabel(S.sim, simProgress);
  $('#simStep').textContent = st.done
    ? `Terminé — développé ${fmt(S.sim.blank_length, 1)} mm`
    : `Coude ${st.index + 1}/${S.sim.bends.length} · `
      + (st.phase === 'rotation'
        ? `rotation ${Number(st.bend.rotation).toFixed(0)}°`
        : `cintrage ${Number(st.bend.angle).toFixed(0)}° réel `
          + `(R15 ${st.bend.r15}°)`)
      + ` · Rm ${st.bend.clr} mm`;
}

function startSim() {
  if (!S.sim || !S.sim.bends.length) {
    say('Pas de coude à simuler sur cette pièce.', 'err');
    return;
  }
  $('#simbar').hidden = false;
  $('#simulate').classList.add('on');
  simProgress = 0;
  simLast = performance.now();
  simPlaying = true;
  $('#simPlay').textContent = '⏸';
  drawSim();
  applyToggles();          // masque maillage, axe, points et cotes
  // Le brut droit est bien plus long que la pièce finie : on recadre sur les
  // deux, sinon le tube sort du champ pendant les premières secondes.
  const blank = centerlineAt(S.sim, 0).map((p) => [p.x, p.y, p.z]);
  frame([...blank, ...S.detail.polyline]);
  say(`Simulation : le tube part droit, à ${fmt(S.sim.blank_length, 0)} mm.`);
}

function stopSim() {
  simPlaying = false;
  $('#simbar').hidden = true;
  $('#simulate').classList.remove('on');
  $('#simPlay').textContent = '▶';
  if (simMesh) {
    model.remove(simMesh);
    simMesh.geometry.dispose();
    simMesh = null;
    if (S.detail) frame(S.detail.polyline);   // retour au cadrage de la pièce
  }
  applyToggles();
}

$('#simulate').onclick = () => (simMesh ? stopSim() : startSim());
$('#simPlay').onclick = () => {
  simPlaying = !simPlaying;
  simLast = performance.now();
  if (simPlaying && S.sim && simProgress >= S.sim.bends.length) simProgress = 0;
  $('#simPlay').textContent = simPlaying ? '⏸' : '▶';
};
$('#simReset').onclick = () => { simProgress = 0; drawSim(); };
$('#simRange').oninput = (e) => {
  if (!S.sim) return;
  simPlaying = false;
  $('#simPlay').textContent = '▶';
  simProgress = (Number(e.target.value) / 1000) * S.sim.bends.length;
  drawSim();
};

function tickSim(now) {
  if (!simPlaying || !S.sim) return;
  const dt = (now - simLast) / 1000;
  simLast = now;
  simProgress += dt * Number($('#simSpeed').value);
  if (simProgress >= S.sim.bends.length) {
    simProgress = S.sim.bends.length;
    simPlaying = false;
    $('#simPlay').textContent = '▶';
  }
  drawSim();
}

/* ═══════════════════════════════════════════════════ onglet Réglages */

function numInput(id, value, ref, step = 'any') {
  const changed = ref !== undefined && ref !== null
    && Number(value) !== Number(ref) ? 'changed' : '';
  const v = value === null || value === undefined ? '' : value;
  return `<input id="${id}" type="number" step="${step}" value="${v}" class="${changed}">`;
}

const MODE_LABELS = {
  entier: 'Arrondi entier — inverse l\'arrondi du programmeur (recommandé)',
  proportionnel: 'Proportionnel — R15 × 90 / R15 à 90°',
  brut: 'Aucune — R15 pris pour l\'angle réel (comportement v4)',
};

async function renderSettings() {
  const host = $('#tab-set');
  if (!S.settings) {
    try {
      S.settings = await api('/api/settings');
    } catch (e) {
      host.innerHTML = `<div class="issue erreur">${esc(e.message)}</div>`;
      return;
    }
  }
  const { config, conventions: convs, angle_modes: modes, reference } = S.settings;

  const cards = Object.entries(config.tooling)
    .sort((a, b) => a[1].diameter - b[1].diameter)
    .map(([name, t]) => {
      const d = Math.round(t.diameter);
      const rmRef = reference.rm[d];
      const elRef = reference.elongation[d];
      const msRef = reference.min_straight[d];
      const wRef = reference.wall[d];
      const r90 = reference.r15_for_90[d];
      return `<div class="tool-card" data-tool="${name}">
        <h4>${name}</h4>
        <span class="ref">Référence BSA — Rm ${rmRef ?? '—'} mm · paroi ${wRef ?? '—'} mm ·
          allongement ${elRef ?? '—'} % · droite mini ${msRef ?? '—'} mm ·
          R15 à 90° = ${r90 ?? '—'}°</span>
        <div class="grid">
          <div><label>Rayon Rm (mm)</label>${numInput(`t_${name}_clr`, t.clr, rmRef, '0.1')}</div>
          <div><label>Paroi (mm)</label>${numInput(`t_${name}_wall`, t.wall, wRef, '0.05')}</div>
          <div><label>Allongement (%)</label>${numInput(`t_${name}_elong`, t.elongation, elRef, '0.1')}</div>
          <div><label>Droite mini (mm)</label>${numInput(`t_${name}_ms`, t.min_straight, msRef, '0.5')}</div>
          <div><label>Angle max (°)</label>${numInput(`t_${name}_maxa`, t.max_angle, reference.max_angle, '1')}</div>
          <div><label>Matière</label><input id="t_${name}_mat" type="text"
               value="${esc(t.material ?? '')}" style="text-align:left"></div>
        </div></div>`;
    }).join('');

  host.innerHTML = `
    <h3 class="sec">Général</h3>
    <div class="set-row"><label>Convention de longueur</label>
      <select id="s_conv">${convs.map((c) => `<option value="${c}"
        ${c === config.convention ? 'selected' : ''}>${c}</option>`).join('')}</select></div>
    <div class="set-row"><label>Correction du retour élastique</label>
      <select id="s_mode">${modes.map((m) => `<option value="${m}"
        ${m === config.angle_mode ? 'selected' : ''}>${MODE_LABELS[m] || m}</option>`).join('')}</select></div>
    <div class="set-row"><label>Sens de rotation (axe B)</label>
      <select id="s_hand">
        <option value="1" ${config.handedness === 1 ? 'selected' : ''}>+1 — sens direct</option>
        <option value="-1" ${config.handedness === -1 ? 'selected' : ''}>−1 — sens inverse (pièce miroir)</option>
      </select></div>
    <p class="note">Le sens de rotation est global. Il ne dépend pas de la tête :
      écrire +180 en tête du bas et −180 en tête du haut, c'est choisir un
      chemin, pas inverser l'axe <span class="src">[DOC 8.3.5]</span>.</p>

    <h3 class="sec">Outillage par diamètre</h3>
    ${cards}

    <h3 class="sec">Limites machine (lecture seule)</h3>
    <dl class="kv">
      <dt>Développé</dt><dd>${reference.developed[0]} – ${reference.developed[2]} mm</dd>
      <dt>Recommandé mini</dt><dd>${reference.developed[1]} mm</dd>
      <dt>Dernier segment max</dt><dd>${reference.max_last} mm</dd>
      <dt>Course axe C</dt><dd>0 – ${reference.max_angle}°</dd>
    </dl>

    <div class="set-actions">
      <button id="s_apply" class="primary">Appliquer</button>
      <button id="s_reset">Réinitialiser</button>
    </div>
    <p class="note">Appliquer relit le fichier ouvert avec les nouvelles
      valeurs, et la pièce sélectionnée est retracée. Un champ en orange
      s'écarte de la valeur de référence de la documentation BSA.</p>`;

  $('#s_apply').onclick = applySettings;
  $('#s_reset').onclick = async () => {
    busy(true);
    try {
      const r = await api('/api/settings/reset', { method: 'POST' });
      S.settings = null;
      await renderSettings();
      await reloadAfterSettings(r);
      say('Réglages remis aux valeurs BSA par défaut.', 'ok');
    } catch (e) {
      say(e.message, 'err');
    } finally {
      busy(false);
    }
  };
}

async function reloadAfterSettings(r) {
  if (!r.reloaded || !r.reloaded.lots) return;
  S.lots = r.reloaded.lots;
  renderList();
  if (S.uid && findTube(S.uid)) await select(S.uid);
}

function readNum(id) {
  const el = $(`#${CSS.escape(id)}`);
  if (!el || el.value === '') return null;
  return Number(el.value);
}

async function applySettings() {
  const cfg = JSON.parse(JSON.stringify(S.settings.config));
  cfg.convention = $('#s_conv').value;
  cfg.angle_mode = $('#s_mode').value;
  cfg.handedness = Number($('#s_hand').value);

  for (const name of Object.keys(cfg.tooling)) {
    const t = cfg.tooling[name];
    t.clr = readNum(`t_${name}_clr`);
    t.wall = readNum(`t_${name}_wall`);
    t.elongation = readNum(`t_${name}_elong`) ?? 0;
    t.min_straight = readNum(`t_${name}_ms`);
    t.max_angle = readNum(`t_${name}_maxa`) ?? 188;
    const mat = $(`#${CSS.escape(`t_${name}_mat`)}`);
    t.material = mat && mat.value ? mat.value : null;
  }

  busy(true);
  try {
    const r = await api('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    S.settings.config = cfg;
    await reloadAfterSettings(r);
    await renderSettings();
    say('Réglages appliqués et pièce retracée.', 'ok');
  } catch (e) {
    say(`Réglages refusés : ${e.message}`, 'err');
  } finally {
    busy(false);
  }
}
