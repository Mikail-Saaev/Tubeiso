# Démarrage

Trois façons d'utiliser tubeiso, de la plus simple à la plus technique.

| | Pour qui | Prérequis |
|---|---|---|
| **1. L'exécutable** | tout le monde | aucun |
| **2. L'application depuis les sources** | vous avez Python | Python 3.11+ |
| **3. La ligne de commande** | traitement par lot | Python 3.11+ |

---

## 1. L'exécutable (recommandé)

Aucune installation, pas de Python. Vous double-cliquez, l'interface s'ouvre.

### Obtenir l'exécutable

**Par GitHub, le plus simple.** Déposez ce projet sur un dépôt GitHub. Le
fichier `.github/workflows/build.yml` fabrique automatiquement les binaires
Windows, macOS et Linux à chaque poussée. Vingt minutes plus tard, ouvrez
l'onglet **Actions**, cliquez sur la dernière exécution et téléchargez
`tubeiso-windows.zip` dans la section *Artifacts*.

Un tag de version (`git tag v1.0.0 && git push --tags`) crée en plus une
*Release* publique avec les trois archives.

**Sur une machine Windows.** Si vous préférez construire localement, il faut
Python **une seule fois**, sur cette machine-là :

```
build_windows.bat
```

Le résultat se trouve dans `dist\tubeiso\`. Sous macOS ou Linux :
`./build_unix.sh`.

### Utiliser l'exécutable

Décompressez l'archive, puis lancez `tubeiso.exe`. Une console s'ouvre et
affiche l'adresse de l'interface, qui se lance ensuite automatiquement.

> **Distribuez le dossier entier**, pas seulement le `.exe`. Le binaire a
> besoin du dossier `_internal` qui l'accompagne.

Vous pouvez aussi lui passer un fichier directement :

```
tubeiso.exe C:\chemin\vers\LFT.xlsx
```

ou déposer le `.xlsx` sur l'icône de l'exécutable.

### Ce à quoi il faut s'attendre au premier lancement

**Le pare-feu Windows peut demander une autorisation.** L'application ouvre un
serveur sur `127.0.0.1`, votre machine uniquement. Vous pouvez **refuser**
l'accès réseau sans aucune conséquence.

**L'antivirus peut être méfiant.** Un exécutable PyInstaller non signé
déclenche parfois une alerte générique. Si votre service informatique impose
la signature de code, le workflow GitHub peut intégrer un certificat.

**Le dossier pèse environ 1,1 Go.** Voir la note sur la taille en fin de page.

---

## 2. L'application depuis les sources

```
pip install -r requirements.txt
python -m tubeiso.app
```

Options utiles :

| Option | Effet |
|---|---|
| `LFT.xlsx` | ouvre un fichier au démarrage |
| `-c tooling.json` | table outillage personnalisée |
| `--mode browser` | force le navigateur plutôt qu'une fenêtre native |
| `--port 9000` | change le port |
| `--debug` | journal détaillé |

Pour une vraie fenêtre d'application plutôt qu'un onglet de navigateur :
`pip install pywebview`.

---

## 3. La ligne de commande

```
python -m tubeiso.cli inspect   LFT.xlsx                  # diagnostic, n'écrit rien
python -m tubeiso.cli plan      LFT.xlsx -o plans         # plans PDF + cahier du lot
python -m tubeiso.cli model     LFT.xlsx -o modeles_3d    # solides STEP
python -m tubeiso.cli calibrate LFT.xlsx                  # contrôle de Rm
python -m tubeiso.cli init                                # tooling.json
```

Commencez toujours par `inspect` sur un fichier inconnu : il ne trace rien, et
il vous dit tout de suite combien de pièces entrent dans le périmètre.

---

## 4. La campagne, pour des milliers de LFT

C'est la commande qui transforme un dossier de LFT en bibliothèque rangée.

```
python -m tubeiso.cli batch D:\LFT -o D:\bibliotheque_tubes ^
       -r D:\Repertoire_Machines_Consolide.xlsm --workers 6
```

ou, avec l'exécutable :

```
tubeiso.exe --cli batch D:\LFT -o D:\bibliotheque_tubes -r Repertoire.xlsm
```

| Option | Effet |
|---|---|
| `-r`, `--repertoire` | `Repertoire_Machines_Consolide.xlsm` : rattache chaque LFT à son groupe et à sa machine |
| `--limit 20` | s'arrêter après 20 fichiers, pour un essai |
| `--no-3d` | sauter les solides STEP — environ cinq fois plus rapide |
| `--no-plans` | ne produire que les modèles et les données |
| `--workers 6` | répartir les fichiers sur 6 processus |
| `--force` | retraiter les LFT déjà faites |
| `--stl` `--brep` `--dxf` | formats supplémentaires |

**Faites toujours un premier passage en reconnaissance :**

```
python -m tubeiso.cli batch D:\LFT -o D:\essai --limit 20 --no-3d
```

Vingt fichiers, aucun solide : quelques secondes. Ouvrez ensuite
`D:\essai\journal.txt`, qui donne la proportion de pièces exploitables et les
motifs d'exclusion. C'est là qu'on voit si le parc est prêt, avant d'engager
plusieurs heures de calcul.

### Ce que vous obtenez

```
bibliotheque_tubes/
  INDEX.xlsx     ← commencez ici
  rapport.csv
  journal.txt
  plans/         BCH_PLATINE_82_0889_0877-0000-CL_223.pdf   ← pour le sous-traitant
  step/          BCH_PLATINE_82_0889_0877-0000-CL_223.stp   ← le solide
  donnees/       BCH_PLATINE_82_0889_0877-0000-CL_223.json  ← les données
  cahiers/       BCH_PLATINE_82_0889_0877-0000-CL_cahier.pdf ← à imprimer
```

Un dossier par type, sans niveau imbriqué : le nom du fichier porte sa LFT et
son repère, donc on retrouve n'importe quel tube par une simple recherche dans
`plans/`. Le groupe et la machine sont des colonnes filtrables d'`INDEX.xlsx`.

`INDEX.xlsx` est la porte d'entrée : une ligne par tube, un filtre sur chaque
colonne, et les chemins du plan, du modèle et du JSON cliquables. Trois
onglets : *Tubes*, *LFT* et *Campagne*.

La campagne est **reprenable** : relancée sur le même dossier de sortie, elle
saute les LFT déjà traitées — l'état est noté dans `.tubeiso-etat.json`, à la
racine. Un fichier illisible n'interrompt rien, il est journalisé.

### Combien de temps

Comptez environ **une seconde par tube** avec les solides STEP, et cinq fois
moins sans. Sur 3 000 programmes, prévoyez donc moins d'une heure avec
`--workers 6`, et quelques minutes avec `--no-3d`.

---

## Utiliser l'interface

**Ouvrir.** Cliquez sur **`Parcourir…`** et choisissez votre fichier LFT
(`.xlsx`) ou votre modèle (`.stp`, `.step`). Le champ de texte reste
disponible si vous préférez coller un chemin.

**Choisir une pièce.** La liste de gauche montre une pastille par pièce :
verte conforme, orange alerte, rouge erreur.

**Naviguer en 3D.** Clic gauche pour tourner, molette pour zoomer, clic droit
pour déplacer. Les boutons `Iso` `X` `Y` `Z` cadrent sur une vue standard,
`Recadrer` remet la pièce entière dans le champ.

**Afficher ou masquer.** `Solide`, `Filaire`, `Axe` (la fibre neutre),
`Points` (tangences en bleu, sommets en rouge), `Cotations`.

**Mesurer.** Activez `Mesurer`, puis cliquez deux points d'accrochage. La
distance affichée est calculée entre les coordonnées exactes, pas entre deux
sommets de triangle.

**Exporter.** `Exporter la pièce` ou `Tout exporter`, puis **`Parcourir…`**
pour choisir le dossier de destination — la fenêtre du système s'ouvre — et
cochez les formats : STEP, STL, BREP, plan PDF, plan SVG, DXF. Les fichiers
sont nommés `<nom du LFT>_<repère>.<extension>`, par exemple
`BCH_PLATINE_82_0889_0877-0000-CL_170.pdf`, pour se rattacher à leur source
sans qu'on ait à les ouvrir.

**Traiter des milliers de LFT.** Le bouton `Campagne…` ouvre la même chose que
la commande `batch` : vous désignez le dossier des LFT, le dossier de sortie et
— facultativement — `Repertoire_Machines_Consolide.xlsm`, et la barre de
progression nomme le fichier en cours. La campagne peut être arrêtée à tout
moment : ce qui est écrit reste écrit, et une relance reprend où elle s'est
arrêtée.

**Simuler le cintrage.** Le bouton `Simuler` rejoue la fabrication de la
pièce : le tube part droit, à sa longueur développée, puis chaque coude se
forme dans l'ordre — d'abord la rotation du plan, puis le pliage, comme sur la
machine. Une barre apparaît avec lecture, pause, curseur de position et
vitesse. L'étape en cours est nommée : numéro du coude, phase, rayon utilisé.

**Les quatre onglets de droite.** *Cotations* donne la table LRA exacte,
*Diagnostic* la liste des contrôles avec leur source dans la documentation
Crippa, *Programme* le code ISO d'origine, *Réglages* la configuration
complète.

**Régler.** L'onglet *Réglages* expose chaque paramètre : convention de
longueur, sens de rotation, tolérance de bouclage, et pour chaque diamètre le
rayon Rm, la paroi, la matière, l'allongement, la droite minimale et l'angle
maximal. La valeur de référence de la documentation BSA est rappelée sous
chaque diamètre, et **tout champ qui s'en écarte passe en orange**.

`Appliquer` relit le fichier ouvert et retrace la pièce sélectionnée
immédiatement : l'effet de chaque réglage est donc visible tout de suite.
`Réinitialiser` remet les valeurs BSA.

---

## Si ça coince

**`python n'est pas reconnu`** — Python n'est pas dans le PATH. Réinstallez en
cochant « Add python.exe to PATH », ou utilisez `py` au lieu de `python`.

**« Noyau CAO absent » dans la barre du bas** — la 3D est indisponible.
Ouvrez `http://127.0.0.1:8731/api/diag` dans un navigateur : cette page liste
chaque module et l'erreur exacte qui l'empêche de se charger.

**`colonne PROGCRIPPA absente`** — votre colonne porte un autre nom. En ligne
de commande, ajoutez `--column NOM_DE_LA_COLONNE`.

**Beaucoup de pièces « hors périmètre »** — c'est normal, et c'est voulu :
l'application ne traite que les tuyaux équipés d'une PROGCRIPPA. Le motif est
affiché pour chacune. `matière_souple` désigne un tuyau qui n'est pas cintré
sur la Crippa ; `tube_droit_sans_programme` un tube laissé droit ;
`programme_tronqué` un export Excel qui a coupé le champ texte à 255
caractères — là, le problème est dans la chaîne d'export, pas dans l'outil.

**Le bouton `Parcourir…` ne fait rien** — la fenêtre de sélection s'appuie sur
Tk, absent de certaines installations Python minimales. Le message vous le dit,
et le champ texte reste utilisable : collez-y le chemin. L'exécutable
distribué, lui, embarque Tk.

**Une campagne est déjà en cours** — une seule à la fois, sinon deux
traitements écriraient dans la même arborescence. Attendez la fin ou cliquez
`Arrêter`.

**`balayage impossible`** — un segment droit est plus court que le rayon de
cintrage, ou deux coudes se suivent sans droite entre eux. Le message nomme la
pièce.

---

## Note sur la taille

Le dossier fait environ **1,1 Go**, dont 630 Mo de VTK. Ce n'est pas de la
négligence : l'extension native du noyau OpenCascade est liée en dur aux
bibliothèques `libvtk*.so`, dont `libvtkWrappingPythonCore`, qui vit dans le
paquet `vtkmodules`. Les retirer casse le chargement du noyau — c'est vérifié,
pas supposé.

Ce qui pouvait partir est parti : `llvmlite`, `numba`, `scipy`, `matplotlib`,
`pandas`, `PIL`. Chaque exclusion a été validée en bloquant réellement le
module puis en rejouant toute la chaîne — balayage, exports, lecture STEP,
tessellation, mise en plan. À l'inverse, `nlopt`, `casadi`, `multimethod` et
VTK se sont révélés indispensables, et sont donc conservés.

Un élagage chirurgical de VTK reste possible, en ne gardant que les
bibliothèques dont `ldd` montre qu'OCP a besoin. Ce n'est pas fait ici parce
qu'un paquet fragile qui casse chez un client est pire qu'un paquet volumineux.

---

## Contenu du dossier

| Fichier | Rôle |
|---|---|
| `tubeiso/` | le code |
| `tubeiso/app/` | serveur et interface |
| `tooling.example.json` | table outillage, à copier en `tooling.json` |
| `tubeiso.spec` | recette PyInstaller |
| `.github/workflows/build.yml` | fabrication automatique des exécutables |
| `build_windows.bat`, `build_unix.sh` | construction locale |
| `exemple_programme_410.txt` | programme complet, pour tester |
| `exemple_plans/`, `exemple_modeles_3d/` | sorties de référence |
| `tests/test_tubeiso.py` | doit afficher une trentaine de `ok` |
