/* Reconstruction de la fibre neutre à un instant donné du cintrage.
 *
 * Ce calcul vit dans son propre module pour être testable hors navigateur :
 * il ne touche ni au DOM ni au rendu. Le fichier `tests/test_simulation.mjs`
 * le confronte à la géométrie calculée par Python, qui fait autorité.
 *
 * `progress` va de 0 (tube droit, à sa longueur développée) à bends.length
 * (pièce terminée). Chaque coude se déroule en deux temps — d'abord la
 * rotation du plan, puis le pliage — parce que c'est l'ordre réel de la
 * machine : l'axe B tourne le tube, puis l'axe C plie.
 */
import * as THREE from './vendor/three.module.min.js';

export const ROT_PART = 0.35;   // part du pas consacrée à la rotation

/* Signe de l'axe B — doit rester identique à `geometry.B_SIGN` côté Python.
 * B positif = rotation horaire du plan de cintrage, vue depuis l'aval, donc
 * rotation NÉGATIVE dans le repère direct. `tests/test_simulation.mjs`
 * compare cette reconstruction à la géométrie Python : si les deux signes
 * divergent, le test tombe. */
export const B_SIGN = -1;

const rad = (d) => (d * Math.PI) / 180;
const arcLength = (b) => b.clr * rad(b.angle);

/** Longueur développée totale : ce que mesure le tube droit avant cintrage. */
export function blankLength(sim) {
  return sim.straights.reduce((a, b) => a + b, 0)
       + sim.bends.reduce((a, b) => a + arcLength(b), 0);
}

export function centerlineAt(sim, progress) {
  const { straights, bends } = sim;
  const hand = sim.handedness ?? 1;

  let p = new THREE.Vector3(0, 0, 0);
  const t = new THREE.Vector3(1, 0, 0);
  const u = new THREE.Vector3(0, 0, 1);
  const pts = [p.clone()];

  // ce qu'il reste de tube droit en aval, coudes non encore formés inclus
  const remaining = (from) => {
    let r = 0;
    for (let j = from; j < straights.length; j += 1) r += straights[j];
    for (let j = from; j < bends.length; j += 1) r += arcLength(bends[j]);
    return r;
  };

  for (let i = 0; i < bends.length; i += 1) {
    p = p.clone().addScaledVector(t, straights[i]);
    pts.push(p.clone());

    const step = Math.min(Math.max(progress - i, 0), 1);
    const rotF = Math.min(step / ROT_PART, 1);
    const bendF = Math.max((step - ROT_PART) / (1 - ROT_PART), 0);

    if (bends[i].rotation && rotF > 0) {
      u.applyAxisAngle(t, B_SIGN * hand * rad(bends[i].rotation) * rotF);
      u.addScaledVector(t, -u.dot(t)).normalize();
    }

    if (bendF <= 0) {                    // coude pas encore amorcé
      p = p.clone().addScaledVector(t, remaining(i + 1) + arcLength(bends[i]));
      pts.push(p.clone());
      return pts;
    }

    const axis = new THREE.Vector3().crossVectors(t, u).normalize();
    const theta = rad(bends[i].angle) * bendF;
    const centre = p.clone().addScaledVector(u, bends[i].clr);
    const radial = p.clone().sub(centre);
    const n = Math.max(3, Math.ceil(24 * bendF));
    for (let k = 1; k <= n; k += 1) {
      pts.push(centre.clone().add(
        radial.clone().applyAxisAngle(axis, (theta * k) / n)));
    }
    p = pts[pts.length - 1].clone();
    t.applyAxisAngle(axis, theta).normalize();
    u.applyAxisAngle(axis, theta);
    u.addScaledVector(t, -u.dot(t)).normalize();

    if (bendF < 1) {                     // pliage en cours : la suite est droite
      p = p.clone().addScaledVector(
        t, remaining(i + 1) + arcLength(bends[i]) * (1 - bendF));
      pts.push(p.clone());
      return pts;
    }
  }

  p = p.clone().addScaledVector(t, straights[straights.length - 1]);
  pts.push(p.clone());
  return pts;
}

/** Longueur de la polyligne, pour contrôler que le métal se conserve. */
export function polylineLength(pts) {
  let total = 0;
  for (let i = 1; i < pts.length; i += 1) total += pts[i].distanceTo(pts[i - 1]);
  return total;
}

/** Étape en cours, pour l'affichage. */
export function stepLabel(sim, progress) {
  if (progress >= sim.bends.length) return { done: true, index: -1, phase: 'terminé' };
  const i = Math.max(0, Math.min(Math.floor(progress), sim.bends.length - 1));
  const step = Math.min(Math.max(progress - i, 0), 1);
  return {
    done: false,
    index: i,
    phase: step < ROT_PART ? 'rotation' : 'cintrage',
    bend: sim.bends[i],
  };
}
