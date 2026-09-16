# tubeiso

Génération de **modèles 3D exploitables** de tubes cintrés à partir des
programmes ISO (G-code, ISO 6983) d'une cintreuse Crippa / SINUMERIK 840D.

Deux livrables par tube, faits pour partir tels quels chez un sous-traitant :

* un **plan PDF autoportant** de deux pages A4, qui suffit à fabriquer la pièce
  sans aucune information complémentaire ;
* un fichier **STEP AP214**, solide creux exact, avec sa fibre neutre nommée.

Trois interfaces : une **application graphique** avec vue 3D et lecteur STEP,
une **ligne de commande** dont la commande `batch` traite des milliers de LFT
d'un coup, et une **bibliothèque Python**. Voir `DEMARRAGE.md`.

```
LFT.xlsx ──lecteur──▶ lots + tubes ──filtre périmètre──▶ tubes avec PROGCRIPPA
                                          │
                                   hors périmètre : motif tracé, aucun fichier
                                          │
                          parseur ──▶ LRA brut ──convention BSA──▶ LRA géométrique
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
python -m tubeiso.cli calibrate LFT.xlsx                # vérifie Rm sur un corpus
python -m tubeiso.cli model     LFT.xlsx -o modeles_3d/ # solides STEP
python -m tubeiso.cli plan      LFT.xlsx -o plans/      # plans PDF + cahier du lot

# la campagne : des milliers de LFT vers une bibliothèque rangée
python -m tubeiso.cli batch  D:\\LFT -o D:\\bibliotheque_tubes ^
       -r Repertoire_Machines_Consolide.xlsm --workers 6
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

## Le plan PDF

Le plan est le seul document que verra le fabricant. Il est écrit pour se
suffire à lui-même : quelqu'un qui n'a jamais entendu parler de la Crippa doit
pouvoir cintrer la pièce à partir de ces deux pages.

**Page 1 — la pièce.** Vue isométrique cotée, avec la longueur de chaque
segment droit, l'angle de chaque coude et sa rotation d'axe B ; trois vues
orthogonales (face, dessus, gauche) ; les extrémités nommées A et B ;
l'encombrement. À droite, quatre blocs : matière, débit, cintrage, extrémités.
Puis le cartouche : repère, programme, lot, LFT, groupe, machine, désignation,
échelle, indice, et le verdict des contrôles automatiques.

**Page 2 — les données.** La table LRA complète (longueur, rotation, angle
réel, R15 programmé, rayon, cumul développé), les coordonnées XYZ de tous les
points d'intersection, le détail des extrémités avec les profondeurs de forage
Vögel et d'emmanchement Ermeto, la liste des contrôles, le programme ISO
d'origine, et sept notes de fabrication.

Ces notes sont la partie la moins spectaculaire et la plus importante. Elles
disent, noir sur blanc, ce qu'un plan de cintrage laisse d'ordinaire implicite :

* les longueurs sont des cotes **tangente à tangente**, pas des cotes de
  sommet — la confusion coûte plusieurs millimètres par coude ;
* la colonne « angle réel » est l'angle du tube fini ; la colonne « R15 » est
  la consigne Crippa, **qui contient déjà sa surcompensation d'élasticité**.
  Un autre moyen de production doit repartir de l'angle réel et appliquer la
  sienne. C'est l'erreur qui produit des pièces fausses en série ;
* la rotation B s'applique **avant** le cintrage du coude concerné, et B ± 360
  sont équivalents ;
* le rayon Rm est celui de la **fibre neutre**.

Le même dessin sort en SVG pour l'aperçu de l'application et en PDF pour la
sous-traitance : `sheet.py` expose une surface de dessin commune, et la mise en
plan est écrite une seule fois. Aucune cote ne peut diverger entre les deux.

Une pièce dont un contrôle est en erreur reçoit un **bandeau rouge en haut de
la page 1** qui nomme l'anomalie, en plus de la mention dans le cartouche.
`batch` trace le plan quand même — il est souvent la meilleure façon de
comprendre ce qui cloche — mais il est impossible de le confondre avec un plan
bon. `plan`, en usage manuel, refuse au contraire de tracer, sauf `--force`.

`plan` et `batch` produisent en plus un **cahier par LFT** : une couverture qui
récapitule le lot, puis tous les plans à la suite. C'est ce qu'on imprime pour
l'atelier ; les PDF individuels sont ce qu'on envoie pièce par pièce.

## Matières souples et matières rigides

`materials.py` transcrit le tableau « N° matière tube et tuyau » de BSA. Six
familles, et une seule est cintrée sur la Crippa :

| Famille | Préfixe | Nature | Cintrable |
|---|---|---|---|
| Ermeto | 293, 416 | tube acier rigide | oui, de Ø4 à Ø18 |
| Pneumatique | 750 42*x*, 758 423 | tuyau souple | non |
| Lubrification | 751, 758 421 | tuyau souple | non |
| Arrosage | 778 | tuyau souple | non |
| Forflex spiralé acier | 750 421 | tuyau souple | non |
| Uniflex noir | 769 | tuyau souple | non |

Le deuxième triplet du code n'est pas décoratif : `750 421 012` est un Forflex
Ø20/13 alors que `750 423 012` est un tuyau pneumatique Ø12/8. Un code absent
du catalogue est quand même classé par son préfixe — c'est la **nature** qui
décide du sort de la pièce, et elle se lit sur trois chiffres.

Les Ø22 à Ø38 existent en Ermeto mais BSA ne les cintre plus : ils sont donc
rigides et hors périmètre, ce que l'application distingue explicitement d'un
tuyau souple.

## Le périmètre : seulement ce qui a une PROGCRIPPA

Une pièce sans programme n'a pas de géométrie. En fabriquer une quand même
revient à livrer un modèle inventé, ce qui est plus dangereux qu'un modèle
absent. `scope.py` tranche avant tout calcul, et chaque exclusion porte un
motif stable :

| Motif | Ce qu'il signifie |
|---|---|
| `matière_souple` | tuyau souple : ni cintré, ni modélisable |
| `tube_droit_sans_programme` | case DROIT cochée, aucun programme |
| `plié_à_la_main` | case FAITMAIN : façonné hors Crippa |
| `sans_programme` | colonne PROGCRIPPA vide |
| `programme_tronqué` | pas de M30 : géométrie fausse mais plausible |
| `hors_outillage_crippa` | diamètre rigide sans matrice BSA |
| `diamètre_introuvable` | ni dans le programme, ni dans CODE_MAT |

Rien n'est perdu pour autant : les pièces exclues figurent dans l'index, dans
le récapitulatif du lot et sur la couverture du cahier, avec leur motif. Une
campagne rend compte de **100 % des lignes lues**.

Dans l'application, chaque pièce porte une pastille `rigide` ou `souple`, les
pièces hors périmètre sont estompées, et l'onglet *Cotations* ouvre sur un bloc
**Matière** qui donne la famille, le code BSA, la paroi et le motif éventuel.

## La campagne : des milliers de LFT

```
tubeiso batch <dossiers…> -o bibliotheque -r Repertoire_Machines_Consolide.xlsm
```

La commande parcourt récursivement les dossiers, lit chaque `.xlsx` / `.xlsm`,
et écrit :

```
bibliotheque/
  INDEX.xlsx                       une ligne par tube, filtrable, avec les liens
  rapport.csv                      le même contenu en texte
  journal.txt                      ce qui s'est passé, fichier par fichier
  BSH/PLATINE_82_0889/BCH_PLATINE_82_0889_0877-0000-CL/
      …_cahier.pdf                 tous les plans du lot
      …_recapitulatif.csv          les pièces du lot
      plans/223.pdf                le plan autoportant
      modeles_3d/223.stp           le solide
      donnees/223.json             toutes les données techniques
```

Le rattachement **Groupe → Machine → LFT** vient de
`Repertoire_Machines_Consolide.xlsm`, qui associe chaque code LFT — c'est-à-dire
chaque nom de fichier — à sa machine et à sa description. Sans ce fichier, le
nom se suffit : `BCH_PLATINE_82_0889_0877-0000-CL` donne le groupe BSH (le
préfixe de fichier `BCH` désigne le groupe `BSH`), la machine `PLATINE_82_0889`
et la liste `0877-0000-CL`.

`INDEX.xlsx` porte trois onglets — *Tubes*, *LFT*, *Campagne* — et vingt-cinq
colonnes par tube, dont les chemins cliquables vers le plan, le modèle et le
JSON. C'est la table de la bibliothèque : un filtre sur `Groupe` + `Contrôle`
sort en deux clics toutes les pièces d'un groupe à revoir.

Trois propriétés comptent à cette échelle :

* **reprenable.** Une LFT déjà traitée est sautée, sauf `--force`. Une campagne
  interrompue redémarre où elle s'était arrêtée.
* **tolérante.** Un classeur illisible est journalisé et la campagne continue.
* **parallèle.** `--workers 6` répartit les fichiers sur plusieurs processus.
  `--no-3d` saute les solides et va cinq fois plus vite, pour un premier
  passage de reconnaissance.

Commencer par `--limit 20` sur un échantillon : le journal dit immédiatement
quelle proportion du parc a une PROGCRIPPA exploitable.

## Ce qui reste à faire

1. **Vérifier le sens de rotation** sur une pièce réelle. Un seul réglage
   global, `handedness` ; une erreur de signe donne une pièce en miroir,
   plausible et immontable. Une seule mesure suffit à trancher, et c'est le
   dernier point qui empêche de signer les plans les yeux fermés.
2. **La séquence de changement de tête** [DOC 5.6] contient des déplacements Y
   de repositionnement que le parseur compte encore comme de l'avance tube.
   Huit programmes sur cent quarante-cinq sont concernés dans le corpus d'essai.
3. **Déduire les profondeurs d'emmanchement** des longueurs du premier et du
   dernier segment quand les extrémités sont serties. Les tables sont en place
   et figurent sur le plan ; la règle de déduction reste à confirmer avec le
   bureau des méthodes.
4. **Les indices de révision.** Le cartouche porte un indice fixe `A` : il
   faudra le faire vivre le jour où un plan est réédité après modification.

## Structure

| Fichier | Rôle |
|---|---|
| `model.py` | Modèle pivot : `Tooling`, `Bend`, `TubeProgram` |
| `materials.py` | Catalogue matière BSA : familles, rigide / souple, cintrable |
| `scope.py` | Périmètre : qui est traité, qui est exclu et pourquoi |
| `parsers/crippa.py` | Lecture syntaxique du dialecte Crippa, sans interprétation |
| `lft.py` | Lecture du LFT : en-tête, regroupement multi-lignes, lots |
| `bsa.py` | Constantes machine, modèle de longueur et retour élastique |
| `conventions.py` | Longueurs machine → longueurs géométriques |
| `geometry.py` | LRA → fibre neutre 3D |
| `validate.py` | Contrôles : développé, droites, angles, auto-collision |
| `solid.py` | Solide 3D balayé et exports STEP / STL / BREP |
| `stepreader.py` | Lecture STEP : maillage et extraction analytique exacte |
| `sheet.py` | Surface de dessin en mm, rendue en SVG ou en PDF |
| `render.py` | Mise en plan deux pages, cahier de lot, export DXF |
| `registry.py` | Rattachement LFT → groupe → machine |
| `batch.py` | Campagne : arborescence, `INDEX.xlsx`, journal, reprise |
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
