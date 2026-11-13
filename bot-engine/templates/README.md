# Gabarits de bonus

Déposez ici une image par type de bonus du jeu, capturée depuis une vraie capture
d'écran. Le moteur les charge au démarrage et reconnaît alors les bonus de façon
exacte (type compris), au lieu de se rabattre sur l'heuristique colorimétrique.

## Noms de fichiers attendus

| Fichier                 | Effet reconnu                     |
| ----------------------- | --------------------------------- |
| `rocket_row.png`        | fusée qui nettoie la ligne         |
| `rocket_column.png`     | fusée qui nettoie la colonne       |
| `plane.png`             | avion                              |
| `bomb.png`              | bombe à effet de zone              |
| `disco.png`             | boule disco (joker)                |
| tout autre nom          | bonus générique (effet de zone 3×3) |

Les synonymes sont reconnus : `row`, `horizontal`, `column`, `vertical`, `tnt`,
`rainbow`, `ball`, `paper`.

## Comment produire un gabarit

1. Récupérez une capture du niveau depuis le téléphone :
   `adb exec-out screencap -p > capture.png`
2. Extrayez la case du bonus avec l'outil fourni :
   `python tools/capture_template.py capture.png --row 4 --col 2 --kind bomb`
3. Poussez le fichier sur le dépôt, puis redéployez le service.

Un gabarit est comparé en corrélation normalisée multi-échelle (0,86 / 1,00 /
1,16). Le seuil est réglable : `vision.template_threshold` dans `config.json`
(défaut 0,75). Recadrez au plus près de la pièce, sans fond de plateau.
