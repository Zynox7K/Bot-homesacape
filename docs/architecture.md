# Architecture

## Principes

Trois contraintes ont dicté tous les choix techniques.

**Zéro ralentissement sur le téléphone.** Aucune bibliothèque de vision, aucun
solveur, aucune dépendance native dans l'APK. Le téléphone photographie, envoie,
reçoit des coordonnées et rejoue un geste. Tout le reste vit sur le serveur.

**Le dépôt est la source de vérité.** Les réglages du moteur sont un fichier
versionné, `bot-engine/config.json`. L'application le récupère via l'API GitHub et
l'applique : un `git push` change le comportement du bot sans reconstruire l'APK.

**Aucun code d'approximation.** Pas de fonction simulée, pas de `TODO`, pas de
branche morte. Chaque étape est testée : 119 tests couvrent la configuration, le
solveur, la vision, le pipeline complet et l'API HTTP.

## Découpage

```
android-app/                      bot-engine/
─────────────                     ───────────
MainActivity (Compose)            engine/api/main.py        FastAPI, CORS, threadpool
   │  réglages, autorisations          │
   ▼                                   ▼
SettingsRepository ────────────▶  engine/service.py         Engine.analyze()
RuntimeBus (StateFlow)                │
   ▲                                  ├─▶ vision/frame.py       décodage, masques, Lab
   │                                  ├─▶ vision/grid.py        localisation de la grille
FloatingOverlayService                ├─▶ vision/classifier.py  cases, palette, obstacles
   │  boucle de pilotage              ├─▶ vision/templates.py   bonus par gabarit PNG
   ▼                                  └─▶ solver/search.py      choix du coup
TouchAccessibilityService             engine/config.py          configuration versionnée
   capture + dispatchGesture
```

## Le téléphone

### Trois composants, trois rôles

`TouchAccessibilityService` est le seul point de contact avec le système. Depuis
Android 11, `AccessibilityService.takeScreenshot()` renvoie un `HardwareBuffer`
sans passer par `MediaProjection` : aucune activité de consentement, aucun dialogue
récurrent, rien de visible pour l'utilisateur pendant la partie.
`dispatchGesture()` rejoue ensuite un glissement dont la trace est celle d'un doigt
(chemin, durée, pression implicite). Le service n'interprète rien : il exécute.

`FloatingOverlayService` est un service de premier plan de type `specialUse`. Il
tient la bulle et la boucle de pilotage. Le type `specialUse` est le seul adapté :
il ne s'agit ni de média, ni de localisation, ni de synchronisation ; le sous-type
est déclaré dans le manifeste (`PROPERTY_SPECIAL_USE_FGS_SUBTYPE`).

`MainActivity` est l'écran Compose d'appairage et de contrôle. Elle ne pilote rien
directement : elle démarre ou arrête le service, et observe `RuntimeBus`.

### Pourquoi la bulle n'est pas en Compose

Une fenêtre superposée ouverte depuis un `Service` n'a ni `LifecycleOwner`, ni
`ViewModelStoreOwner`, ni `SavedStateRegistryOwner`. Un `ComposeView` y exige de
fournir ces trois propriétaires manuellement. `BubbleView` est une `View` dessinée
à la main : zéro plomberie, zéro allocation pendant le glissement, et un rendu
stable alors que le jeu tourne en dessous. L'écran de contrôle, lui, est
entièrement en Compose.

### Modèle de fils d'exécution

| Étape                              | Fil                          | Durée typique |
| ---------------------------------- | ---------------------------- | ------------- |
| Capture d'écran                    | exécuteur principal système  | 50 – 150 ms   |
| Réduction + encodage JPEG          | `Dispatchers.Default`        | 15 – 40 ms    |
| Appel réseau `POST /analyze`       | `Dispatchers.IO`             | 150 – 450 ms  |
| Exécution du geste                 | fil principal (callback)     | 40 – 400 ms   |
| Attente de stabilisation           | `delay` sur `Default`        | 1 250 ms      |
| Mise à jour de la bulle            | `Handler(Looper.getMainLooper())` | < 1 ms   |

La boucle tourne dans une coroutine unique sur `Dispatchers.Default` : jamais sur le
fil principal, jamais en concurrence avec elle-même. Les appels au service
d'accessibilité sont transformés en fonctions suspendues par
`suspendCancellableCoroutine`, bornées par `withTimeoutOrNull` — si le système ne
rappelle jamais la fin d'un geste, le pilote repart au tour suivant au lieu de se
figer.

### Coordonnées normalisées

Le moteur répond en coordonnées **relatives à l'image reçue**, entre 0 et 1. Le
téléphone les multiplie par la taille réelle de son écran. Aucune résolution n'est
négociée à l'avance, et la réduction de l'image avant envoi (1080 px → 540 px) ne
change rien au geste rejoué.

### Garde-fous

* **Pause hors du jeu** : les événements `TYPE_WINDOW_STATE_CHANGED` indiquent
  l'application au premier plan. Dès qu'elle diffère du paquet surveillé, le pilote
  se met en pause — il ne glissera jamais sur votre messagerie.
* **Arrêt sur échecs répétés** : cinq échecs consécutifs (capture refusée, moteur
  injoignable, geste annulé) arrêtent le pilote et le signalent.
* **Intervalle minimal** : Android limite `takeScreenshot` à environ une capture par
  seconde. L'intervalle est borné à 1 000 ms côté application, et le moteur renvoie
  son propre `capture_interval_ms` que le pilote respecte.
* **Verrou d'activité partiel** avec expiration (4 h) : l'écran du jeu peut
  s'éteindre sans interrompre une analyse en cours, et le verrou ne peut pas rester
  pris indéfiniment.

## Le moteur

### Vision

1. **Décodage** — `decode_image` accepte PNG, JPEG, WebP. L'image est réduite à
   `analysis_width` (540 px) : c'est la résolution de travail de toute la chaîne.
2. **Masques** — deux masques de saturation/luminosité sont calculés : `vivid`
   (pixels colorés, pour la classification) et `strong` (pixels très saturés, pour
   la localisation). Le bandeau d'objectif et les boutons du jeu saturent `vivid` :
   localiser sur ce masque échouerait, d'où le second.
3. **Fenêtres candidates** — recherche de fenêtres carrées notées
   `intérieur − 0,55 × couronne + 0,06 × priorité centrale`, rejet sous 0,12,
   suppression des doublons à 0,45, au plus 12 candidates.
4. **Période** — autocorrélation par FFT sur les deux axes, affinage parabolique
   sous-pixel, puis balayage de phase `align_axis` sur ±0,5 pas (pas de 0,05). Sans
   ce balayage, la grille peut être décalée d'un dixième de case et chaque cellule
   échantillonne alors l'espace inter-case.
5. **Extension** — `extend_grid` fait croître la grille tant que la densité au
   centre des nouvelles cases reste supérieure à 0,32 et dépasse d'au moins 0,12
   celle de l'espace inter-case : les plateaux non carrés (7 × 8, 8 × 9) sont ainsi
   couverts alors que la recherche initiale ne teste que des fenêtres carrées.
6. **Palette** — k-means circulaire sur la teinte, initialisation k-means++,
   8 redémarrages, 32 itérations, pénalité de séparation (`separation_ratio` 1,8),
   plus grand nombre de couleurs admissible, rejet des teintes isolées. Aucune couleur
   n'est codée en dur :
   le moteur s'adapte aux thèmes et aux événements du jeu.
7. **Cases** — pour chaque cellule : médiane Lab sur un disque central, taux de
   pixels colorés, quatre sondes de coin et quatre sondes d'interstice. Une case est
   un **obstacle** si le contraste relatif de ses coins dépasse `block_corner_ratio`,
   si son taux coloré est faible, ou si sa teinte est isolée dans la palette. Elle
   est un **trou** si rien ne la distingue du fond.
8. **Bonus** — comparaison par corrélation normalisée multi-échelle (0,86 / 1,00 /
   1,16) avec les gabarits PNG de `bot-engine/templates/`, seuil
   `template_threshold` (0,75). Sans gabarit, une heuristique d'étalement de teinte
   (> 40°) prend le relais et signale un bonus générique.

Le critère de coin est **relatif** : distance de chaque coin au fond local mesuré
dans les interstices, rapportée à la distance centre-fond de la cellule, avec un
plancher `corner_min_contrast`. Une comparaison absolue de couleur casse dès que la
luminosité du jeu change ; la forme relative tient de 0,45× à 1,4× de luminosité,
avec bruit, flou et décalage de teinte — c'est ce que vérifient les tests.

### Solveur

Deux passes, pour rester dans un budget de temps fixe :

1. `rank_moves` évalue tous les échanges légaux par simulation déterministe
   (alignements, cascades, gravité, création et déclenchement de bonus,
   combinaisons de bonus).
2. Les `candidates` meilleurs sont repris par des tirages de Monte-Carlo
   (`playouts`), moyennés, puis combinés au score déterministe à 0,75 / 0,25.

La gravité traite les trous et les obstacles intacts comme des solides qui
segmentent les colonnes : une pièce ne tombe pas à travers une caisse. Le rechargement
est stochastique en tirage normal et déterministe en pré-classement.

Poids par défaut : cellule 1,0 · obstacle 7,0 · couleur cible ×2,2 · cascade 0,22 ·
déclenchement de bonus 6,0 · fusée 12 · avion 18 · bombe 26 · disco 34 · combinaison
de bonus 1,35 par cellule · bonus de profondeur 0,05 · biais de position 0,04.

### Encodage des cases

| Valeur                     | Signification                                    |
| -------------------------- | ------------------------------------------------ |
| `-4`                       | case déjà libérée (simulation)                   |
| `-3`                       | case inconnue (rechargement déterministe)        |
| `-2`                       | trou, hors plateau                               |
| `-1`                       | obstacle (caisse, carton, glace…)                |
| `0 … n-1`                  | couleur, indice dans la palette détectée         |
| `100 + couleur × 8 + type` | bonus : 0 fusée ligne, 1 fusée colonne, 2 avion, 3 bombe, 4 disco, 5 générique |

### Latence mesurée

Sur un conteneur 2 vCPU, capture 1080 × 2400 réduite à 540 px, JPEG qualité 78
(61 Ko envoyés), médiane de cinq analyses :

| Mode         | Vision  | Solveur | Total moteur |
| ------------ | ------- | ------- | ------------ |
| `fast`       | 104 ms  | 8 ms    | **114 ms**   |
| `balanced`   | 104 ms  | 30 ms   | **136 ms**   |
| `expert`     | 102 ms  | 104 ms  | **214 ms**   |

Sur captures dégradées (0,55× et 1,35× de luminosité, bruit σ=7, flou 1,6,
teinte −14°) : grille 9 × 9 retrouvée dans les cinq cas, fiabilité ≥ 0,998,
environ 200 ms.

Un cycle complet côté téléphone — capture, envoi, réponse, geste, stabilisation —
dure donc 1,7 à 2,1 s, dont 1,25 s d'attente volontaire des animations du jeu.

### Configuration

`EngineConfig` regroupe `VisionConfig` et `SolverConfig`. Le fichier
`bot-engine/config.json` est chargé au démarrage (`AUTOPLAY_CONFIG` peut pointer
ailleurs) et peut être remplacé à chaud par `POST /config`.

Trois niveaux se combinent :

1. le fichier du dépôt, appliqué au démarrage ou poussé à chaud ;
2. le champ `config` d'une requête, qui se fusionne par-dessus ;
3. le mode de calcul (`fast`, `balanced`, `expert`), qui ne remplit que les champs
   **absents** de la source : un réglage explicite n'est jamais écrasé par un preset.

## Tests

| Fichier                        | Couverture                                                        |
| ------------------------------ | ----------------------------------------------------------------- |
| `tests/test_config.py`         | chargement, sauvegarde, fusion, presets, champs explicites         |
| `tests/test_solver.py`         | alignements, cascades, gravité segmentée, bonus, blocages          |
| `tests/test_vision_grid.py`    | localisation, période, sous-pixel, plateaux non carrés             |
| `tests/test_vision_classifier.py` | palette, pièces, obstacles, trous, captures dégradées           |
| `tests/test_pipeline.py`       | bout en bout : optimalité du coup, obsolescence, latence          |
| `tests/test_api.py`            | HTTP : multipart, base64, erreurs, CORS, configuration protégée   |
| `tests/test_templates.py`      | gabarits : reconnaissance, faux positifs, plateau inchangé        |

Les captures de test sont synthétiques (`tests/synthetic.py`) : écran 1080 × 2400
complet avec bandeau d'objectif, cadre de plateau, caisses, trous et bonus, plus un
générateur de dégradations. Elles ne dépendent d'aucune image du jeu, donc d'aucun
droit d'auteur, et elles sont reproductibles par graine.
