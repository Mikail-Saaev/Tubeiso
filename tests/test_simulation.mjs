/* Confronte la simulation JavaScript a la geometrie Python.
 *
 *   node tests/test_simulation.mjs
 *
 * Le fichier attendu est produit par test_simulation_ref.py, qui utilise
 * tubeiso.geometry — la reference qui fait autorite.
 */
import { readFileSync } from 'node:fs';
import { centerlineAt, blankLength, polylineLength, stepLabel }
  from '../tubeiso/app/static/simulation.js';

const ref = JSON.parse(readFileSync(new URL('./simulation_ref.json', import.meta.url)));
let echecs = 0;

function verifie(nom, condition, detail = '') {
  if (condition) {
    console.log(`ok  ${nom}`);
  } else {
    echecs += 1;
    console.log(`ECHEC  ${nom}  ${detail}`);
  }
}

for (const cas of ref.cases) {
  const sim = cas.simulation;
  const n = sim.bends.length;

  // 1. A progression nulle, le tube est droit et mesure le developpe.
  const droit = centerlineAt(sim, 0);
  const lDroit = polylineLength(droit);
  verifie(`${cas.ref} — tube droit = developpe`,
    Math.abs(lDroit - cas.developed) < 1e-6,
    `${lDroit.toFixed(4)} vs ${cas.developed}`);

  // 2. La longueur se conserve a chaque instant : on ne cree pas de metal.
  let conserve = true;
  for (let k = 0; k <= 40; k += 1) {
    const l = polylineLength(centerlineAt(sim, (k / 40) * n));
    if (Math.abs(l - cas.developed) > 0.35) conserve = false;   // discretisation
  }
  verifie(`${cas.ref} — longueur conservee pendant le pliage`, conserve);

  // 3. A progression complete, la piece coincide avec la geometrie Python.
  const fini = centerlineAt(sim, n);
  const bout = fini[fini.length - 1];
  const attendu = cas.end_point;
  const ecart = Math.hypot(bout.x - attendu[0], bout.y - attendu[1],
                           bout.z - attendu[2]);
  verifie(`${cas.ref} — extremite finale conforme a Python`,
    ecart < 0.05, `ecart ${ecart.toFixed(4)} mm`);

  // 4. Le developpe recalcule par le module correspond.
  verifie(`${cas.ref} — blankLength coherent`,
    Math.abs(blankLength(sim) - cas.developed) < 1e-6);

  // 5. Le libelle d'etape suit bien les deux phases.
  const s0 = stepLabel(sim, 0.1);
  const s1 = stepLabel(sim, 0.8);
  verifie(`${cas.ref} — phases rotation puis cintrage`,
    s0.phase === 'rotation' && s1.phase === 'cintrage' && stepLabel(sim, n).done);
}

console.log(echecs ? `\n${echecs} echec(s).` : '\nSimulation conforme a la geometrie Python.');
process.exit(echecs ? 1 : 0);
