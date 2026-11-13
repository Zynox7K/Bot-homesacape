# Mise au point

Comment travailler sur le moteur : environnement, tests, outils de diagnostic,
gabarits de bonus et réglages de la vision.

---

## 1. Environnement

```bash
cd bot-engine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt      # moteur + pytest + httpx
python -m pytest tests                   # 119 tests, ~15 s
```

Python 3.11 ou 3.12. `opencv-python-headless` suffit : aucune dépendance à X11,
le moteur tourne aussi bien dans un conteneur que sur un poste de travail.

Démarrer le service localement :

```bash
AUTOPLAY_ADMIN_KEY="ma-cle" uvicorn engine.api.main:app --reload --port 8080
curl localhost:8080/docs
```

---

## 2. Tests

| Fichier                        | Ce qu'il verrouille                                                    |
| ------------------------------ | ---------------------------------------------------------------------- |
| `test_config.py`               | chargement, sauvegarde, fusion, presets, priorité des champs explicites |
| `test_solver.py`               | alignements, cascades, gravité segmentée par les obstacles, bonus, combinaisons, plateaux bloqués |
| `test_vision_grid.py`          | localisation, période, affinage sous-pixel, plateaux non carrés         |
| `test_vision_classifier.py`    | palette, pièces, obstacles, trous, captures dégradées                   |
| `test_pipeline.py`             | bout en bout : le coup renvoyé est l'optimum glouton du plateau réel, obsolescence, latence |
| `test_api.py`                  | HTTP : multipart, base64, erreurs 400/403, CORS, configuration protégée |
| `test_templates.py`            | gabarits : reconnaissance exacte, aucun faux positif, plateau inchangé   |

Les captures de test sont **synthétiques** (`tests/synthetic.py`) : un écran
1080 × 2400 complet, avec bandeau d'objectif, cadres arrondis, caisses, trous et
bonus, généré par graine. Aucune image du jeu n'est nécessaire, donc aucun droit
d'auteur, et les résultats sont reproductibles.

```python
from synthetic import build_level, degrade

level = build_level(rows=9, cols=9, colors=5, seed=41,
                    blocks=((2, 2), (6, 5)), holes=((0, 0),),
                    specials=((4, 4, KIND_BOMB),))
level.values            # la vérité terrain, case par case
level.cell_center(4, 4) # coordonnées en pixels d'une case
capture = degrade(level.image, brightness=0.55, noise_sigma=7, blur=1.6, seed=3)
```

`degrade` simule ce qu'un téléphone produit réellement : luminosité, bruit, flou,
décalage de teinte. Les tests de vision l'utilisent pour exiger la même grille et
la même partition pièce/obstacle/trou sur six variantes d'une même capture.

Ajouter un cas : écrivez-le d'abord dans `tests/`, faites-le échouer, puis corrigez
le moteur. Les seuils de la vision se règlent toujours sur des captures dégradées,
jamais sur la capture propre — c'est là que les régressions apparaissent.

---

## 3. Outils de diagnostic

### `tools/analyze_file.py`

Analyse une capture, localement ou contre un moteur déployé.

```bash
# analyse locale : le moteur s'exécute dans ce processus
python tools/analyze_file.py capture.png --show-board
python tools/analyze_file.py capture.png --mode expert --debug annotated.png
python tools/analyze_file.py capture.png --json > resultat.json

# contrôle d'un déploiement : même appel que le téléphone
python tools/analyze_file.py capture.png --url https://autoplay-engine.onrender.com
python tools/analyze_file.py capture.png --url https://… --cell-sheet cases.png
```

Sortie : état, grille et pas détectés, fiabilité, contenu du plateau, coup retenu
avec son explication, minutage détaillé et, au choix, l'image annotée ou la planche
des cases classifiées. Le code de retour vaut 0 si un coup a été trouvé — il
s'intègre donc dans un script de vérification.

Récupérer une capture du téléphone :

```bash
adb exec-out screencap -p > capture.png
```

### `tools/capture_template.py`

Extrait une case d'une capture et l'enregistre comme gabarit de bonus.

```bash
python tools/capture_template.py capture.png --row 4 --col 2 --kind bomb
# grille 9 × 9 (fiabilité 1.00) — gabarit bomb écrit : templates/bomb.png (42 × 42 px)
```

---

## 4. Gabarits de bonus

Sans gabarit, le moteur détecte les bonus par étalement de teinte (plus de 40°
dans une case) : il sait qu'il y a un bonus, pas lequel. Avec des gabarits, il
reconnaît le type exact — fusée ligne, fusée colonne, avion, bombe, disco — par
corrélation normalisée multi-échelle (0,86 / 1,00 / 1,16), seuil
`template_threshold` (0,75).

| Fichier             | Effet reconnu                     |
| ------------------- | --------------------------------- |
| `rocket_row.png`    | fusée qui nettoie la ligne         |
| `rocket_column.png` | fusée qui nettoie la colonne       |
| `plane.png`         | avion                              |
| `bomb.png`          | bombe à effet de zone              |
| `disco.png`         | boule disco (joker)                |
| autre nom           | bonus générique (effet de zone 3×3) |

Synonymes reconnus dans les noms : `row`, `horizontal`, `column`, `vertical`,
`tnt`, `rainbow`, `ball`, `paper`.

Procédure : capturez un niveau contenant chaque bonus, extrayez-les avec
`capture_template.py`, poussez les PNG dans `bot-engine/templates/`, redéployez.
Les gabarits sont versionnés : ils partagent le cycle de revue du reste du code.

Recadrez au plus près de la pièce, sans fond de plateau : la corrélation se fait
sur la case entière redimensionnée à `template_size` (48 px).

---

## 5. Réglages de la vision

`bot-engine/config.json`, section `vision`. Ceux qui servent vraiment :

| Clé                     | Défaut | Effet                                                                |
| ----------------------- | ------ | -------------------------------------------------------------------- |
| `analysis_width`        | 540    | résolution de travail ; 420 accélère, 720 affine les petites cases     |
| `min_saturation`        | 0,30   | seuil du masque `vivid` (pixels colorés)                              |
| `min_value`             | 0,22   | seuil de luminosité du même masque                                    |
| `min_periodicity`       | 0,20   | régularité minimale pour accepter une grille                          |
| `min_pitch_px`          | 18     | plus petite case acceptée, en pixels d'analyse                        |
| `max_aspect_ratio`      | 1,60   | rapport maximal entre les pas horizontal et vertical                  |
| `block_corner_ratio`    | 0,22   | contraste relatif de coin au-delà duquel une case est un obstacle      |
| `corner_min_contrast`   | 6,0    | plancher de contraste centre-fond : en dessous, la case n'est pas notée |
| `corner_probe_ratio`    | 0,38   | position des sondes de coin (fraction du pas)                         |
| `gap_probe_ratio`       | 0,06   | rayon des sondes d'interstice qui mesurent le fond local              |
| `block_max_vivid`       | 0,34   | taux de pixels colorés en dessous duquel une case est un obstacle      |
| `palette_min` / `_max`  | 3 / 8  | nombre de couleurs cherchées                                           |
| `palette_min_gap_deg`   | 16     | écart de teinte minimal entre deux couleurs                           |
| `separation_ratio`      | 1,8    | exigence de séparation des groupes de teinte                          |
| `special_hue_spread_deg`| 40     | étalement de teinte qui signale un bonus sans gabarit                 |
| `template_threshold`    | 0,75   | score de corrélation minimal pour reconnaître un gabarit              |

Section `solver` :

| Clé              | Défaut     | Effet                                                     |
| ---------------- | ---------- | --------------------------------------------------------- |
| `mode`           | `expert`   | `fast` 8 candidats/1 tirage, `balanced` 14/6, `expert` 24/16 |
| `goal_color`     | -1         | couleur prioritaire (×2,2 sur son poids)                  |
| `position_bias`  | 0,04       | léger biais vers le centre du plateau                     |
| `random_seed`    | 20240117   | graine des tirages : résultats reproductibles             |
| `weights.*`      | voir fichier | cellule 1,0 · obstacle 7,0 · cascade 0,22 · fusée 12 · avion 18 · bombe 26 · disco 34 |

Un champ présent dans `config.json` ou dans le champ `config` d'une requête est
**explicite** : le preset du mode ne l'écrase jamais. Pour laisser le mode piloter
un paramètre, retirez-le du fichier.

---

## 6. Symptômes et correctifs

| Symptôme                                     | Cause                                                        | Correctif                                                            |
| -------------------------------------------- | ------------------------------------------------------------ | -------------------------------------------------------------------- |
| Grille détectée trop petite (4 × 4 sur un 8 × 8) | plateau non carré : la recherche ne teste que des fenêtres carrées | `extend_grid` s'en charge ; vérifiez `max_aspect_ratio` et les seuils de densité |
| Toutes les cases lues comme trous            | les pièces débordent dans les interstices, le fond local est faussé | les sondes d'interstice ignorent déjà les pixels `vivid` ; augmentez `gap_probe_ratio` si le jeu a des marges larges |
| Caisse prise pour une pièce                  | contraste de coin trop faible sur ce thème                    | baissez `block_corner_ratio` vers 0,18                                |
| Pièce prise pour une caisse                  | thème très contrasté                                          | remontez `block_corner_ratio` vers 0,28                               |
| Deux couleurs fusionnées dans la palette     | teintes proches (deux verts)                                  | augmentez `separation_ratio` ou baissez `palette_min_gap_deg`         |
| Grille décalée d'une demi-case               | phase mal alignée                                             | déjà couvert par `align_axis` (balayage ±0,5 pas) ; vérifiez `min_pitch_px` sur les très grandes cases |
| Bonus non reconnus                           | aucun gabarit dans `templates/`                               | créez-les avec `capture_template.py`                                  |
| Faux bonus sur des pièces brillantes         | seuil d'étalement de teinte trop bas                          | remontez `special_hue_spread_deg` ou `template_threshold`             |
| Analyse lente (> 1 s)                        | mode `expert`, image trop grande                              | passez en `balanced`, réduisez `analysis_width`                       |
| `Plateau inchangé depuis la dernière analyse` | le niveau n'a pas bougé entre deux captures                  | normal : le `session_id` est stable et la grille identique            |

Chaque correctif se valide de la même façon :

```bash
python -m pytest tests -q                       # rien ne régresse
python tools/analyze_file.py capture.png --show-board --debug annotated.png
```

L'image annotée montre la grille, l'identifiant de chaque case et le coup retenu :
elle suffit généralement à comprendre ce que le moteur a vu.
