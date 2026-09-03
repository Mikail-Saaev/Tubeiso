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

Pour traiter des milliers de programmes sans interface.

```
python -m tubeiso.cli inspect   LFT.xlsx                       # diagnostic
python -m tubeiso.cli model     LFT.xlsx -o modeles_3d         # solides STEP
python -m tubeiso.cli plan      LFT.xlsx -o plans --dxf        # plans 2D
python -m tubeiso.cli calibrate LFT.xlsx                       # contrôle de Rm
python -m tubeiso.cli init                                     # tooling.json
```

Commencez toujours par `inspect` sur un fichier inconnu : il ne trace rien et
signale immédiatement les programmes tronqués.

---

## Utiliser l'interface

**Ouvrir.** Collez le chemin d'un fichier LFT (`.xlsx`) ou d'un modèle
(`.stp`, `.step`) dans la barre du haut, puis Entrée.

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

**Exporter.** `Exporter la pièce` ou `Tout exporter`, puis choisissez le
dossier et les formats.

**Les trois onglets de droite.** *Cotations* donne la table LRA exacte,
*Diagnostic* la liste des contrôles avec leur source dans la documentation
Crippa, *Programme* le code ISO d'origine.

---

## Si ça coince

**`python n'est pas reconnu`** — Python n'est pas dans le PATH. Réinstallez en
cochant « Add python.exe to PATH », ou utilisez `py` au lieu de `python`.

**« Noyau CAO absent » dans la barre du bas** — la 3D est indisponible.
Ouvrez `http://127.0.0.1:8731/api/diag` dans un navigateur : cette page liste
chaque module et l'erreur exacte qui l'empêche de se charger.

**`colonne PROGCRIPPA absente`** — votre colonne porte un autre nom. En ligne
de commande, ajoutez `--column NOM_DE_LA_COLONNE`.

**Toutes les pièces sont ignorées** — lisez le motif affiché. Si c'est
« programme tronqué », le problème vient de l'export Excel : un champ texte
limité à 255 caractères quelque part dans la chaîne, pas de l'outil.

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
| `tests/test_tubeiso.py` | doit afficher 10 `ok` |
