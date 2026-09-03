# tubeiso

Génération de **modèles 3D exploitables** de tubes cintrés à partir des
programmes ISO (G-code, ISO 6983) d'une cintreuse Crippa / SINUMERIK 840D.

Livrable principal : un fichier **STEP AP214** par tube, prêt pour la
sous-traitance. Les plans 2D sont un livrable secondaire, de contrôle.

Trois interfaces : une **application graphique** avec vue 3D et lecteur STEP,
une **ligne de commande** pour le traitement par lot, et une **bibliothèque
Python**. Voir `DEMARRAGE.md` pour l'installation.

```
LFT.xlsx  ──parseur──▶  LRA brut  ──convention BSA──▶  LRA géométrique
                                                              │
                                                     contrôles machine
                                                              │
                                              fibre neutre 3D exacte
                                            (droites + arcs de cercle)
                                                              │
                                    ┌─────────────────────────┴───────────┐
                                    ▼                                     ▼
                        balayage d'une couronne                  projection isométrique
                                    │                                     │
                        ┌───────────┴──────────┐                ┌─────────┴────────┐
                        ▼                      ▼                ▼                  ▼
                   STEP AP214              STL / BREP      plan SVG A4         DXF
              (sous-traitance)          (visualisation)     (contrôle)      (reprise CAO)
```

## Installation

```bash
pip install -r requirements.txt
```

`cadquery` (noyau OpenCascade, ~300 Mo) n'est requis que pour la commande
`model`. Les autres commandes fonctionnent sans lui.

## Utilisation

```bash
python -m tubeiso.cli init                              # crée tooling.json
python -m tubeiso.cli inspect   LFT.xlsx                # diagnostic, ne trace rien
python -m tubeiso.cli calibrate LFT.xlsx                # cherche rayon + convention
python -m tubeiso.cli model     LFT.xlsx -c tooling.json -o modeles_3d/
python -m tubeiso.cli plan      LFT.xlsx -c tooling.json -o plans/ --dxf
```

## L'application

```
python -m tubeiso.app            # ou tubeiso.exe une fois empaquetée
```

Serveur local + interface WebGL. Ce choix n'est pas un pis-aller : le moteur 3D
du navigateur est déjà installé partout, ce qui supprime tout problème de
pilote graphique ou de contexte OpenGL, et rend l'application identique sous
Windows, macOS et Linux. Le serveur n'écoute que sur `127.0.0.1`.

| Module | Rôle |
|---|---|
| `app/server.py` | API JSON : ouverture, géométrie, exports, diagnostic |
| `app/launcher.py` | démarrage, fenêtre native ou navigateur |
| `app/static/` | interface, three.js embarqué pour un fonctionnement hors ligne |
| `app/static/simulation.js` | calcul du cintrage pas à pas, isolé et testé |

### Le lecteur STEP

`stepreader.py` lit un `.stp` à deux niveaux. La **tessellation** produit un
maillage, qui ne sert qu'à l'affichage. L'**extraction analytique** lit les
surfaces exactes du B-Rep : un tube balayé est fait de faces cylindriques
(les parties droites) et toriques (les coudes), chacune portant son axe, son
rayon et son angle balayé exacts.

Les cotations affichées viennent toujours du second niveau. Un segment de
63,591 mm est rendu comme 63,591 mm, pas comme 63,59 ± la flèche de
tessellation.

L'aller-retour est sans perte. Sur le tube 409, la relecture du STEP retrouve
Ø8,000, paroi 1,000, Rm 14,00, segments 18,000 / 183,000 / 44,511, angles
65,00° et 19,00°, développé 266,036 — exactement les valeurs du modèle source.

### Empaquetage

`tubeiso.spec` produit un exécutable autonome. `.github/workflows/build.yml`
le construit pour Windows, macOS et Linux à chaque poussée sur GitHub, sans
rien installer localement. La recette a été construite et testée : le binaire
démarre sans Python, charge un LFT, génère le maillage et exporte le STEP.

## Le modèle 3D

La fibre neutre est construite en **primitives exactes** — segments de droite
et arcs de cercle — et non en polyligne échantillonnée. Une couronne
(diamètre extérieur moins épaisseur de paroi) est balayée le long de ce
chemin, ce qui donne un solide creux géométriquement exact.

Le fichier STEP contient deux entités nommées :

| Entité | Contenu |
|---|---|
| `tube_<rep>` | le solide, tube creux |
| `fibre_neutre_<rep>` | la ligne d'axe, en droites et arcs |

La seconde permet au sous-traitant de retrouver directement les points de
cintrage sans avoir à les ré-extraire du solide — c'est ce qu'attendent les
logiciels de cintrage qui reconstruisent le LRA depuis un STEP.

`solid.report()` retourne le volume, l'encombrement et un contrôle
d'étanchéité du solide, à vérifier avant expédition.

## Le modèle métier, entièrement sourcé

Toutes les constantes viennent de deux sources et sont regroupées dans
`bsa.py`, chacune avec sa référence :

- `FORMATION_CRIPPA_002` — tables p.4, règles machine chap. 5, méthode CATIA
  chap. 8
- `archivage_crippa_EDT_09_1.xlsm`, `Feuil2` — les formules de calcul

### Modèle de longueur

Transcription littérale des formules de `Feuil2` :

```
arcs    = 0.0174444 × Σ(R15) × Rm            (B2, B3, B6 → B4)
Lg_théo = arcs + Σ(Y) + recoupe              (B7)
R6      = Lg_théo − arcs × allongement%/100  (H7, « Lg. réel tube »)
```

avec `Rm` = 11 mm (Ø4 et Ø6), 14 (Ø8), 23 (Ø10), 30 (Ø12), 45 (Ø15/16),
52 (Ø18), et un allongement de 3 % (Ø4), 5 % (Ø6 à Ø12), 4 % (Ø15/18).

### Les trois règles de lecture

1. **Un segment droit = somme signée des `Y` de son bloc.** Le programmeur
   écrit `Y-35` puis `Y-(L−35)` dès que L dépasse 35, exactement la formule
   `=IF(Y>35, Y-35, "")` de la feuille Excel. La somme est signée pour
   absorber l'astuce anti-collision du chapitre 5.8.

2. **Le premier segment est `R12`, le dernier n'est pas écrit** — il se déduit
   de `R6`. `DS` n'est qu'un commentaire d'aide, et n'est pas fiable.

3. **Les `Y` sont des longueurs tangente-à-tangente, arrondies à 0,5 mm.**
   Vérifié contre CATIA sur le tube 410 : 33,861 → 34 et 63,591 → 63,5.

### Point ouvert : la compensation d'élasticité

Sur trois pièces sur quatre, `Σ(R15)` dépasse de 2 à 4,6 % l'angle qui ferait
tomber le dernier segment exactement sur `DS` — un ordre de grandeur qui
correspond à la colonne « Élasticité » de la doc. `R15` semble donc porter la
compensation de retour élastique, alors que la formule Excel l'utilise brut.

L'écart sur la géométrie est de 1 à 2 mm sur le dernier segment. L'option
`use_true_angles=True` de `BSAConvention.build()` applique la correction.
**Une mesure d'angle dans CATIA sur un seul tube tranche la question.**

## Le garde-fou central

Le développé recalculé est comparé au développé déclaré (`R6` / colonne
`LONGUEUR`). C'est le seul juge de paix disponible :

| Résultat | Interprétation |
|---|---|
| écart < 0,5 mm sur toutes les pièces | convention et rayon justes |
| écart systématique et non nul | convention fausse |
| écart aléatoire | données d'entrée corrompues |

`plan` **refuse de tracer** une pièce dont le développé ne concorde pas, ou
dont le programme est tronqué. `--force` passe outre pour inspection, mais le
cartouche porte alors la mention ERREUR.

## Ce qui reste à faire

1. **Réexporter les programmes tronqués** à 255 caractères. Sur 7 pièces du
   fichier d'essai, 4 sont coupées. Sur 10 000 programmes, c'est le premier
   chantier. `inspect` en donne le décompte immédiatement.
2. **Trancher la compensation d'élasticité** par une mesure d'angle CATIA sur
   un tube. Deux minutes de travail, 1 à 2 mm de précision à la clé.
3. **Vérifier le sens de rotation** sur une pièce réelle. `bsa.rotation_sign()`
   applique la règle de la doc (horaire positif en tête du bas), mais une
   erreur de signe donne une pièce en miroir, plausible et inmontable.
4. **Modéliser les extrémités** : embouts `V04`/`V06`/`V08` (sertissage Vögel)
   et profondeurs de forage du chap. 8.1.2, qui conditionnent les longueurs
   du premier et du dernier segment.
5. **Traitement par lot** sur les 10 000 programmes, avec un rapport
   récapitulatif des pièces conformes, alertées et rejetées.

## Structure

| Fichier | Rôle |
|---|---|
| `model.py` | Modèle pivot : `Tooling`, `Bend`, `TubeProgram` |
| `parsers/crippa.py` | Lecture syntaxique du dialecte Crippa, sans interprétation |
| `bsa.py` | Constantes machine et modèle de longueur, avec sources |
| `conventions.py` | Longueurs machine → longueurs géométriques |
| `geometry.py` | LRA → fibre neutre 3D |
| `validate.py` | Contrôles : développé, droites, angles, auto-collision |
| `solid.py` | Solide 3D balayé et exports STEP / STL / BREP |
| `stepreader.py` | Lecture STEP : maillage et extraction analytique exacte |
| `render.py` | Plan isométrique SVG (A4) et export DXF |
| `calibrate.py` | Vérification de `Rm` sur un corpus |
| `config.py` | `tooling.json` et lecture du fichier LFT |
| `cli.py` | Ligne de commande |

## Ajouter une autre machine

Écrire un parseur qui produit un `RawProgram`, et le brancher. Le reste de la
chaîne est indépendant de la machine — c'est tout l'intérêt du pivot LRA, qui
est le format standard de l'industrie du tube (aussi appelé YBC).


## La simulation de cintrage

Le bouton `Simuler` rejoue la fabrication : le tube part droit à sa longueur
développée, puis chaque coude se forme dans l'ordre de la machine — rotation
de l'axe B, puis pliage de l'axe C.

Le calcul vit dans `app/static/simulation.js`, sans dépendance au DOM ni au
rendu, précisément pour être vérifiable. `tests/test_simulation.mjs` le
confronte à la géométrie Python, qui fait autorité :

| Contrôle | Ce qu'il garantit |
|---|---|
| tube droit = développé | l'état initial est le bon flan |
| longueur conservée à chaque image | aucun métal créé ni perdu pendant le pliage |
| extrémité finale = géométrie Python | la simulation aboutit bien à la pièce réelle |
| phases rotation puis cintrage | l'ordre des mouvements machine est respecté |

La référence est régénérée par `tests/simulation_ref.py`, et les deux tests
tournent dans la chaîne d'intégration à chaque poussée.
