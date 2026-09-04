# tubeiso

Génération de **modèles 3D exploitables** de tubes cintrés à partir des
programmes ISO (G-code, ISO 6983) d'une cintreuse Crippa / SINUMERIK 840D.

Livrable principal : un fichier **STEP AP214** par tube, prêt pour la
sous-traitance. Les plans 2D sont un livrable secondaire, de contrôle.

Trois interfaces : une **application graphique** avec vue 3D et lecteur STEP,
une **ligne de commande** pour le traitement par lot, et une **bibliothèque
Python**. Voir `DEMARRAGE.md` pour l'installation.

```
LFT.xlsx ──lecteur──▶ lots + tubes ──parseur──▶ LRA brut ──convention BSA──▶ LRA géométrique
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

### Le retour élastique — tranché

`R15` **n'est pas** l'angle du tube. C'est l'angle à commander pour obtenir
l'angle voulu une fois l'élasticité relâchée. La documentation donne le R15
d'un pli réel à 90° : 93° en Ø4, 92° en Ø6/8/10, 92,5° en Ø12/15, 93° en Ø18
[DOC 5.4]. Le supplément est proportionnel à l'angle — « pour un angle de 45°,
divisé par deux env. l'angle additionnel » — et le programmeur l'**arrondit**
avant de l'ajouter, comme le montre l'annotation du 412 : « R15=46, en réalité
45° mais faut ajouter 1° pour élasticité ».

`bsa.real_angle()` inverse cette opération. Trois modes, réglables dans
l'interface :

| Mode | Traitement | Ø6, R15 = 46 |
|---|---|---|
| `entier` (défaut) | inverse l'arrondi du programmeur | 45° |
| `proportionnel` | `R15 × 90 / R15₉₀` | 45,00° |
| `brut` | aucune correction (comportement ≤ v4) | 46° |

Le mode `entier` restitue des angles ronds sur tout le corpus : 90, 45, 91,
88, 75, 43, 30. Il boucle exactement — `programmed_angle(real_angle(x)) == x`.

Toute la chaîne de longueur travaille aussi sur l'angle réel. C'est
contre-intuitif mais c'est ce que dit le [DOC 8.3.4] : la colonne `R15` du
classeur *Archivage* reçoit l'angle **mesuré dans Catia**, et le supplément
n'est ajouté qu'au moment d'écrire le programme. C'est donc l'angle réel qui a
produit le `R6` gravé dans le programme. Le corpus le confirme : l'écart au
`DS` tombe de **1,22 mm à 0,41 mm** en moyenne quadratique.

## Le garde-fou central

Jusqu'à la v4 on comparait le développé recalculé à `R6`. Ce contrôle **ne
pouvait pas échouer** : le dernier segment était déduit de `R6` par la même
équation, donc l'algèbre se simplifiait et l'écart valait exactement zéro sur
toutes les pièces, y compris avec un rayon faux.

Il est remplacé par les deux seuls témoins réellement indépendants du calcul :

| Témoin | Origine | Seuils |
|---|---|---|
| `DS` | commentaire du programme, écrit au mm | ±0,75 info · ±2 alerte · au-delà erreur |
| `LONGUEUR` | colonne de la LFT, quand elle diffère de `R6` | ±0,5 mm |

`plan` refuse de tracer une pièce dont le programme est tronqué. `--force`
passe outre pour inspection, mais le cartouche porte alors la mention ERREUR.

## La lecture du LFT

Le gabarit LFT vient de Wire2000 : une liste de fils détournée pour des tubes,
75 colonnes dont la plupart vides. Sa mise en page varie d'un export à l'autre,
et rien ne garantit qu'un tube tienne sur une seule ligne. `lft.py` ne suppose
donc **rien** :

- l'en-tête est cherché dans **toutes** les feuilles et à n'importe quelle
  ligne, par reconnaissance des noms de colonnes — pas par position ;
- les noms sont normalisés (accents, casse, espaces, tirets) et une table de
  synonymes couvre les variantes (`Prog Crippa`, `Repère`, `Long.`…) ;
- les lignes d'un même tube sont regroupées quel que soit leur ordre, y compris
  une ligne de continuation sans aucune identité ;
- **aucune valeur n'est perdue** : chaque colonne conserve la liste ordonnée de
  toutes les valeurs distinctes rencontrées, avec le numéro de ligne d'origine.
  L'onglet *LFT* les affiche toutes, y compris celles que l'application
  n'exploite pas ;
- les tubes sont regroupés par **lot** (colonne `LISTE`), et chaque lot est
  encadré et étiqueté dans la liste de gauche.

Deux notions à ne jamais confondre, et que le code sépare explicitement :

| | Colonne | Exemple | Sens |
|---|---|---|---|
| **Liste / lot** | `LISTE` | `0792-0002-JV` | numéro de LFT |
| **Programme** | `PROGRAMME` | `792_JV-412` | numéro du programme |

Un même repère peut exister dans deux lots : les pièces sont donc identifiées
par un `uid` stable, jamais par leur repère, et le doublon est signalé.

## Ce qui reste à faire

1. **Réexporter les programmes tronqués** à 255 caractères. L'extraction
   `07920002jv.xlsx` montre que la base, elle, n'est pas tronquée.
2. **Vérifier le sens de rotation** sur une pièce réelle. Un seul réglage
   global, `handedness` ; une erreur de signe donne une pièce en miroir,
   plausible et immontable.
3. **Modéliser les extrémités** : embouts `V04`/`V06`/`V08` (sertissage Vögel)
   et profondeurs de forage du chap. 8.1.2, qui conditionnent les longueurs
   du premier et du dernier segment.
4. **Traitement par lot** sur les 10 000 programmes, avec un rapport
   récapitulatif des pièces conformes, alertées et rejetées.

## Structure

| Fichier | Rôle |
|---|---|
| `model.py` | Modèle pivot : `Tooling`, `Bend`, `TubeProgram` |
| `parsers/crippa.py` | Lecture syntaxique du dialecte Crippa, sans interprétation |
| `lft.py` | Lecture du LFT : en-tête, regroupement multi-lignes, lots |
| `bsa.py` | Constantes machine, modèle de longueur et retour élastique |
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
