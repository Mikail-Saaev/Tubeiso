import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';

/* ══════════════════════════════════════════════════════ état de l'application */

const S = {
  tubes: [],          // résumé des pièces
  ref: null,          // repère sélectionné
  detail: null,       // géométrie complète de la pièce affichée
  snaps: [],          // points de accrochage pour la mesure
  measuring: false,
  picked: [],         // points cliqués en cours de mesure
  measures: [],       // mesures validées
  framePts: null,     // points servant au recadrage
  show: { mesh: true, wire: false, axis: true, nodes: true, dims: true },
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const fmt = (v, n = 2) => Number(v).toFixed(n);

function say(msg, kind = '') {
  const el = $('#status');
  el.textContent = msg;
  el.style.color = kind === 'err' ? 'var(--err)'
                 : kind === 'ok' ? 'var(--ok)' : '';
}
const busy = (on) => { $('#spinner').hidden = !on; };

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
camera.position.set(220, 170, 220);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.09;

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const key = new THREE.DirectionalLight(0xffffff, 1.5);
key.position.set(1, 1.4, 0.9);
scene.add(key);
const fill = new THREE.DirectionalLight(0x9fc6ff, 0.5);
fill.position.set(-1, -0.4, -0.8);
scene.add(fill);

const grid = new THREE.GridHelper(1000, 40, 0x2b323d, 0x21262e);
grid.material.transparent = true;
grid.material.opacity = 0.5;
scene.add(grid);
scene.add(new THREE.AxesHelper(28));

const model = new THREE.Group();     // contient la pièce, recentrée sur l'origine
scene.add(model);

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
};

let parts = {};   // sous-objets de `model`, pour les bascules d'affichage
let labels = [];  // { pos: Vector3, text, cls }

function clearModel() {
  model.clear();
  parts = {};
  labels = [];
  S.snaps = [];
  S.picked = [];
  S.measures = [];
  $('#labels').innerHTML = '';
}

function vec(a) { return new THREE.Vector3(a[0], a[1], a[2]); }

/** Recentre la pièce sur l'origine et recadre la caméra. */
function frame(points) {
  S.framePts = points;
  const box = new THREE.Box3();
  points.forEach((p) => box.expandByPoint(vec(p)));
  const c = box.getCenter(new THREE.Vector3());
  model.position.set(-c.x, -c.y, -c.z);
  const size = box.getSize(new THREE.Vector3()).length() || 100;
  grid.scale.setScalar(Math.max(size / 400, 0.25));
  controls.target.set(0, 0, 0);
  const fov = THREE.MathUtils.degToRad(camera.fov);
  const d = (size / 2) / Math.tan(fov / 2) * 1.18;
  camera.position.set(d * 0.58, d * 0.47, d * 0.58);
  camera.near = size / 500;
  camera.far = size * 40;
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
  parts.wire = new THREE.Mesh(g, MAT.wire);
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
  const rT = Math.max(scale * 0.014, 0.9);
  const sphere = new THREE.SphereGeometry(rT, 16, 12);

  const add = (pts, mat, kind) => pts.forEach((p, i) => {
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
  let seg = 0, bend = 0;
  for (const p of detail.primitives) {
    const mid = p.kind === 'line'
      ? [(p.start[0] + p.end[0]) / 2, (p.start[1] + p.end[1]) / 2,
         (p.start[2] + p.end[2]) / 2]
      : p.mid;
    if (p.kind === 'line') {
      labels.push({ pos: vec(mid), text: `${fmt(p.length, 2)}`, cls: 'len' });
      seg += 1;
    } else {
      const rot = detail.bends[bend]?.rotation ?? 0;
      const txt = rot ? `${fmt(p.angle, 1)}°  ↻${fmt(rot, 1)}°`
                      : `${fmt(p.angle, 1)}°`;
      labels.push({ pos: vec(mid), text: txt, cls: 'ang' });
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
  if (detail.mesh_error) {
    say(`Solide non généré : ${detail.mesh_error}`, 'err');
  }
}

/** Affiche un STEP lu depuis le disque (pas issu d'un programme). */
function showStep(res) {
  clearModel();
  const pts = res.lra.vertices.length ? res.lra.vertices
            : [res.bbox.min, res.bbox.max];
  const scale = Math.max(...res.bbox.size, 1);
  buildMesh(res.mesh);
  if (res.lra.vertices.length > 1) buildAxis(res.lra.vertices);

  const grp = new THREE.Group();
  const sphere = new THREE.SphereGeometry(Math.max(scale * 0.014, 0.9), 16, 12);
  res.lra.vertices.forEach((p, i) => {
    const m = new THREE.Mesh(sphere, MAT.vert);
    m.renderOrder = 6;
    m.position.copy(vec(p));
    m.userData = { point: p, kind: 'raccord', index: i };
    grp.add(m);
    S.snaps.push(m);
  });
  parts.nodes = grp;
  model.add(grp);

  labels = [];
  res.features.forEach((f) => {
    const mid = [(f.start[0] + f.end[0]) / 2, (f.start[1] + f.end[1]) / 2,
                 (f.start[2] + f.end[2]) / 2];
    labels.push({
      pos: vec(mid), cls: f.kind === 'line' ? 'len' : 'ang',
      text: f.kind === 'line' ? fmt(f.length, 3) : `${fmt(f.angle, 2)}°`,
    });
  });
  applyToggles();
  frame(pts);
  $('#hint').style.display = 'none';
}

/* ────────────────────────────────────────────────────────────── bascules 3D */

function applyToggles() {
  if (parts.mesh) parts.mesh.visible = S.show.mesh;
  if (parts.wire) parts.wire.visible = S.show.wire;
  if (parts.axis) parts.axis.visible = S.show.axis;
  if (parts.nodes) parts.nodes.visible = S.show.nodes;
  $('#labels').style.display = S.show.dims ? '' : 'none';
}

/* ──────────────────────────────────────────────────── étiquettes projetées */

const labelPool = [];
function drawLabels() {
  const host = $('#labels');
  const all = [...labels, ...S.measures.map((m) => ({
    pos: m.mid, text: `${fmt(m.dist, 3)} mm`, cls: 'mes',
  }))];
  while (labelPool.length < all.length) {
    const d = document.createElement('div');
    d.className = 'lab';
    host.appendChild(d);
    labelPool.push(d);
  }
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const p = new THREE.Vector3();
  const placed = [];
  all.forEach((l, i) => {
    const el = labelPool[i];
    p.copy(l.pos).add(model.position).project(camera);
    const visible = p.z > -1 && p.z < 1;
    el.style.display = visible ? '' : 'none';
    if (!visible) return;
    el.className = `lab ${l.cls}`;
    el.textContent = l.text;
    const x = (p.x * 0.5 + 0.5) * w;
    let y = (-p.y * 0.5 + 0.5) * h;
    for (let j = 0; j < i; j += 1) {
      const o = placed[j];
      if (o && Math.abs(o.x - x) < 74 && Math.abs(o.y - y) < 17) y = o.y + 19;
    }
    placed[i] = { x, y };
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
  });
  for (let i = all.length; i < labelPool.length; i += 1) {
    labelPool[i].style.display = 'none';
  }
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
    say(`Distance exacte : ${fmt(dist, 4)} mm`, 'ok');
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
  S.measures.forEach((m) => model.remove(m.line));
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

function loop() {
  requestAnimationFrame(loop);
  controls.update();
  renderer.render(scene, camera);
  if (S.show.dims) drawLabels();
  const d = camera.position.length();
  $('#readout').textContent = S.detail
    ? `Ø${S.detail.diameter}  ·  ${S.detail.bends.length} coudes  ·  `
      + `développé ${fmt(S.detail.developed, 2)} mm  ·  zoom ${fmt(d, 0)}`
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
    const label = i === 0 ? 'R12 (départ)' : isLast ? 'Dernier segment' : `Segment ${i + 1}`;
    rows.push(`<tr><td>${label}</td><td class="num">${fmt(s, 3)}</td>
               <td class="num">—</td><td class="num">—</td></tr>`);
    if (!isLast) {
      const b = d.bends[bend];
      rows.push(`<tr><td>Coude ${bend + 1}</td><td class="num">—</td>
                 <td class="num">${fmt(b.angle, 2)}°</td>
                 <td class="num">${fmt(b.rotation, 2)}°</td></tr>`);
      bend += 1;
    }
  });

  $('#tab-dims').innerHTML = `
    <h3 class="sec">Identification</h3>
    <dl class="kv">
      <dt>Repère</dt><dd>${d.ref}</dd>
      <dt>Diamètre</dt><dd>Ø${d.diameter}${d.wall ? ` × ${d.wall}` : ''}</dd>
      <dt>Matière</dt><dd>${d.material || '—'}</dd>
      <dt>Outillage</dt><dd>${d.tooling} · ${d.head}</dd>
      <dt>Rayon Rm</dt><dd>${d.bend_radius ?? '—'} mm</dd>
    </dl>

    <h3 class="sec">Cotations exactes (LRA)</h3>
    <table>
      <thead><tr><th>Élément</th><th style="text-align:right">L</th>
        <th style="text-align:right">Angle</th><th style="text-align:right">Rot.</th></tr></thead>
      <tbody>${rows.join('')}
        <tr class="tot"><td>Développé</td>
          <td class="num">${fmt(d.developed, 3)}</td><td></td><td></td></tr>
      </tbody>
    </table>

    <h3 class="sec">Contrôle</h3>
    <dl class="kv">
      <dt>R6 déclaré</dt><dd>${d.declared ?? '—'} mm</dd>
      <dt>Recoupe</dt><dd>${d.recut || 0} mm</dd>
      <dt>Encombrement</dt><dd>${d.bbox.size.map((v) => fmt(v, 1)).join(' × ')}</dd>
    </dl>
    <p class="empty" style="text-align:left;padding:10px 0 0">
      Valeurs analytiques, issues des primitives exactes — jamais mesurées
      sur le maillage d'affichage.</p>`;
}

function renderStepDims(res) {
  const l = res.lra;
  const rows = [];
  const nSeg = l.segments.length;
  for (let i = 0; i < nSeg; i += 1) {
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
      <dt>Fichier</dt><dd>${res.file}</dd>
      <dt>Diamètre lu</dt><dd>${l.tube_radius ? `Ø${fmt(l.tube_radius * 2, 3)}` : '—'}</dd>
      <dt>Paroi</dt><dd>${l.wall ? `${fmt(l.wall, 3)} mm` : '—'}</dd>
      <dt>Rayons Rm</dt><dd>${[...new Set(l.bend_radii)].map((v) => fmt(v, 2)).join(', ') || '—'}</dd>
      <dt>Volume</dt><dd>${res.volume ? `${fmt(res.volume, 1)} mm³` : '—'}</dd>
      <dt>Encombrement</dt><dd>${res.bbox.size.map((v) => fmt(v, 2)).join(' × ')}</dd>
    </dl>
    <h3 class="sec">Cotations extraites (exactes)</h3>
    <table>
      <thead><tr><th>Élément</th><th style="text-align:right">L</th>
        <th style="text-align:right">Angle</th><th style="text-align:right">Rot.</th></tr></thead>
      <tbody>${rows.join('')}
        <tr class="tot"><td>Développé</td>
          <td class="num">${fmt(l.developed, 3)}</td><td></td><td></td></tr>
      </tbody>
    </table>
    <p class="empty" style="text-align:left;padding:10px 0 0">
      Lues dans les surfaces exactes du B-Rep (cylindres et tores), pas
      mesurées sur le maillage.</p>`;

  $('#tab-diag').innerHTML = res.warnings.length
    ? res.warnings.map((w) => `<div class="issue alerte">${w}</div>`).join('')
    : '<div class="issue info">Aucune anomalie détectée à la lecture.</div>';
  $('#tab-prog').innerHTML = '<p class="empty">Fichier STEP : pas de programme source.</p>';
}

function renderDiag(d) {
  $('#tab-diag').innerHTML = d.issues.length
    ? d.issues.map((i) => `<div class="issue ${i.level}">
         <div><strong>${i.code}</strong><br>${i.message}
         ${i.source ? `<code>source : ${i.source}</code>` : ''}</div></div>`).join('')
    : '<div class="issue info">Aucune anomalie. La pièce est conforme.</div>';
}

/* ═══════════════════════════════════════════════════════════ panneau gauche */

function renderList() {
  const q = $('#filter').value.trim().toLowerCase();
  const items = S.tubes.filter((t) => !q || t.ref.toLowerCase().includes(q));
  $('#count').textContent = String(S.tubes.length);
  $('#list').innerHTML = items.map((t) => `
    <li data-ref="${t.ref}" class="${t.ref === S.ref ? 'on' : ''}">
      <i class="dot ${t.status === 'erreur' ? 'err' : t.status === 'alerte' ? 'warn' : 'info'}"></i>
      <span class="ref">${t.ref}</span>
      <span class="meta">Ø${t.diameter} · ${t.bends}c</span>
    </li>`).join('') || '<p class="empty">Aucune pièce.</p>';

  $$('#list li').forEach((li) => {
    li.onclick = () => select(li.dataset.ref);
  });
}

async function select(ref) {
  S.ref = ref;
  renderList();
  busy(true);
  say(`Chargement du repère ${ref}…`);
  try {
    const d = await api(`/api/tube/${encodeURIComponent(ref)}`);
    showTube(d);
    renderDims(d);
    renderDiag(d);
    $('#tab-prog').innerHTML = `<pre class="prog">${
      (d.source || '').replace(/[<&]/g, (c) => (c === '<' ? '&lt;' : '&amp;'))}</pre>`;
    say(`Repère ${ref} — ${d.status === 'erreur' ? 'erreurs détectées'
        : d.status === 'alerte' ? 'alertes' : 'conforme'}.`,
        d.status === 'erreur' ? 'err' : d.status === 'info' ? 'ok' : '');
  } catch (e) {
    say(e.message, 'err');
  } finally {
    busy(false);
  }
}

/* ══════════════════════════════════════════════════════════════════ actions */

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
      S.tubes = []; S.ref = null; renderList();
      showStep(res);
      renderStepDims(res);
      say(`STEP lu : ${res.features.length} éléments reconnus.`, 'ok');
    } else {
      const res = await api('/api/open', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      S.tubes = res.tubes;
      renderList();
      const ok = S.tubes.filter((t) => t.status !== 'erreur').length;
      say(`${res.count} pièces — ${ok} exploitables, ${res.count - ok} à corriger.`);
      if (S.tubes.length) await select(S.tubes[0].ref);
    }
  } catch (e) {
    say(e.message, 'err');
  } finally {
    busy(false);
  }
};

$('#path').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#open').click(); });
$('#filter').oninput = renderList;

$$('[data-toggle]').forEach((b) => {
  b.onclick = () => {
    const k = b.dataset.toggle;
    S.show[k] = !S.show[k];
    b.classList.toggle('on', S.show[k]);
    applyToggles();
  };
});

$$('[data-view]').forEach((b) => {
  b.onclick = () => {
    const d = camera.position.length();
    const v = { iso: [0.62, 0.5, 0.62], x: [1, 0, 0], y: [0, 1, 0.001], z: [0, 0, 1] }[b.dataset.view];
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
  };
});

/* ------------------------------------------------------------------ exports */

let exportScope = 'one';
$('#exportOne').onclick = () => { exportScope = 'one'; $('#exportDlg').showModal(); };
$('#exportAll').onclick = () => { exportScope = 'all'; $('#exportDlg').showModal(); };

$('#doExport').onclick = async (e) => {
  e.preventDefault();
  const formats = $$('#exportDlg fieldset input:checked').map((i) => i.value);
  const dir = $('#outDir').value.trim() || '.';
  const refs = exportScope === 'all' ? S.tubes.map((t) => t.ref)
                                     : (S.ref ? [S.ref] : []);
  $('#exportDlg').close();
  if (!refs.length) { say('Aucune pièce sélectionnée.', 'err'); return; }
  if (!formats.length) { say('Choisissez au moins un format.', 'err'); return; }
  busy(true);
  try {
    const res = await api('/api/export', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refs, dir, formats }),
    });
    const msg = `${res.written.length} fichier(s) écrit(s) dans ${res.dir}`;
    say(res.failed.length ? `${msg} — ${res.failed.length} échec(s) : `
        + res.failed.map((f) => f.ref).join(', ') : msg,
        res.failed.length ? 'err' : 'ok');
  } catch (err) {
    say(err.message, 'err');
  } finally {
    busy(false);
  }
};

/* ───────────────────────────────────────────────────────────── démarrage */

api('/api/status').then((s) => {
  $('#cad').textContent = s.cad ? 'Noyau CAO actif' : 'Noyau CAO absent — 3D indisponible';
  $('#cad').style.color = s.cad ? '' : 'var(--warn)';
  // ?path=... permet d'ouvrir directement un fichier, pratique pour un raccourci
  const q = new URLSearchParams(location.search).get('path');
  const start = q || s.source;
  if (start) { $('#path').value = start; $('#open').click(); }
}).catch(() => say('Serveur injoignable.', 'err'));
