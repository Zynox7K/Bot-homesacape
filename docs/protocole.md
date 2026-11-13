# Protocole réseau

Contrat entre l'application Android et le moteur. Toutes les coordonnées sont
**normalisées entre 0 et 1**, relativement à l'image envoyée : le téléphone les
multiplie par la taille réelle de son écran, aucune résolution n'est négociée.

Base : `https://votre-moteur/` — CORS ouvert (`*`), toutes les réponses en JSON
UTF-8, erreurs au format `{"ok": false, "error": "...", "detail": "..."}`.

---

## `GET /health`

Sonde de vie. Utilisée par Render/Railway, par le bouton **Tester la connexion**
de l'application et par le script CI.

```json
{
  "status": "ok",
  "version": "1.0.0",
  "uptime_s": 1234.5,
  "templates": ["bomb", "plane"],
  "solver_mode": "expert"
}
```

`templates` liste les gabarits de bonus chargés depuis `bot-engine/templates/`.

---

## `POST /analyze`

L'appel principal : une capture d'écran contre un geste à jouer.

### Transport 1 — `multipart/form-data` (recommandé)

C'est celui qu'utilise l'application : pas de surcoût base64, donc 33 % de bande
passante et de latence en moins.

| Champ             | Type    | Requis | Rôle                                                   |
| ----------------- | ------- | ------ | ------------------------------------------------------ |
| `image`           | fichier | oui    | Capture PNG, JPEG ou WebP                              |
| `session_id`      | texte   | non    | Identifiant de partie, pour détecter les plateaux figés |
| `display_width`   | entier  | non    | Largeur de l'écran du téléphone, en pixels             |
| `display_height`  | entier  | non    | Hauteur de l'écran du téléphone, en pixels             |
| `goal_color`      | entier  | non    | Indice de couleur prioritaire (objectif du niveau)     |
| `config`          | JSON    | non    | Réglages fusionnés par-dessus la configuration active  |
| `debug`           | booléen | non    | Renvoie l'image annotée en base64                      |
| `cell_sheet`      | booléen | non    | Renvoie la planche des cases classifiées               |

```bash
curl -s -X POST https://votre-moteur/analyze \
  -F "image=@capture.jpg" \
  -F "session_id=8f1c1a3e" \
  -F "display_width=1080" \
  -F "display_height=2400" \
  -F 'config={"solver":{"mode":"balanced"}}'
```

### Transport 2 — `application/json`

Même contenu, image en base64. Utile pour un script ou un client sans multipart.

```bash
curl -s -X POST https://votre-moteur/analyze \
  -H 'Content-Type: application/json' \
  -d "{\"image_base64\": \"$(base64 -w0 capture.jpg)\", \"session_id\": \"8f1c1a3e\"}"
```

Champs : `image_base64` (requis), `session_id`, `display_width`, `display_height`,
`goal_color`, `config`, `debug`, `cell_sheet`.

### Réponse

Réponse réelle obtenue sur une capture 1080 × 2400 d'un niveau 9 × 9 avec deux
caisses, un trou et deux bonus (`tools/analyze_file.py capture.png --json`, mode
`balanced`). `board` est tronquée aux deux premières lignes pour la lisibilité.

```json
{
  "ok": true,
  "message": "",
  "move": {
    "kind": "swipe",
    "from": { "x": 0.503704, "y": 0.543919 },
    "to": { "x": 0.503704, "y": 0.588970 },
    "duration_ms": 180,
    "score": 153.833,
    "reason": "Bonus déclenché : disco · 2 obstacle(s) · 73 case(s) libérée(s)",
    "combo": false
  },
  "grid": {
    "rows": 9,
    "cols": 9,
    "confidence": 1.0,
    "pitch": 0.100114,
    "origin": { "x": 0.103249, "y": 0.363715 },
    "bounds": { "left": 0.053192, "top": 0.341189, "right": 0.954215, "bottom": 0.746649 }
  },
  "board": [[-2, 0, 3, "..."], [2, 0, 1, "..."]],
  "palette": ["#f22c25", "#f2de25", "#2cf225", "#25a0f2", "#c225f2"],
  "background": "#2c3428",
  "confidence": 0.9999,
  "pieces": 78,
  "blocks": 2,
  "holes": 1,
  "specials": 2,
  "settle_ms": 1250,
  "capture_interval_ms": 1000,
  "stale": false,
  "stale_count": 0,
  "timing": {
    "decode_ms": 29.52,
    "vision_ms": 213.62,
    "solver_ms": 38.63,
    "render_ms": 0.0,
    "total_ms": 281.91
  },
  "debug_image": null,
  "engine_version": "1.0.0",
  "solver_mode": "balanced"
}
```

`pitch` est normalisé par la largeur de l'image, `origin.x` par la largeur et
`origin.y` par la hauteur : `bounds` suit la même convention. Le client n'a besoin
que de `move`, dont les deux points sont normalisés axe par axe.

Points à connaître côté client :

* **`from` et `to`** sont les clés JSON du geste (alias de `source` et `target`
  dans le modèle Pydantic). `to` vaut `null` pour `kind: "tap"` : un appui simple,
  par exemple pour activer un bonus déjà posé.
* **`duration_ms`** est la durée recommandée du geste. L'application la multiplie
  par son réglage « Durée des gestes » puis la borne entre 40 ms et 2 s.
* **`settle_ms`** est le temps à attendre avant la capture suivante, pour laisser
  les animations du jeu se terminer. L'application attend
  `max(intervalle réglé, settle_ms, capture_interval_ms)`.
* **`board`** est la matrice des cases, encodée comme suit :

| Valeur                     | Signification                                       |
| -------------------------- | --------------------------------------------------- |
| `-4`                       | case libérée (simulation interne)                   |
| `-3`                       | case inconnue                                       |
| `-2`                       | trou, hors plateau                                  |
| `-1`                       | obstacle (caisse, carton, glace…)                   |
| `0 … n-1`                  | couleur, indice dans `palette`                      |
| `100 + couleur × 8 + type` | bonus : 0 fusée ligne, 1 fusée colonne, 2 avion, 3 bombe, 4 disco, 5 générique |

* **`stale`** passe à `true` quand le plateau renvoyé est identique au précédent
  pour un même `session_id` : le niveau n'a probablement pas bougé depuis la
  dernière capture.
* **`debug_image`** n'est rempli que si `debug` ou `cell_sheet` est demandé ;
  c'est un PNG en base64. `cell_sheet` est prioritaire.

### Erreurs

| Code | Cas                                                            |
| ---- | -------------------------------------------------------------- |
| 400  | Champ `image` absent, image vide, base64 invalide, JSON illisible |
| 413  | Image trop lourde                                              |
| 403  | `POST /config` sans clé ou avec une clé erronée                |

Une analyse qui échoue à trouver la grille renvoie **200** avec `ok: false` et un
`message` explicite : ce n'est pas une erreur de transport, le client doit la
traiter comme « niveau non reconnu ».

---

## `GET /config`

Configuration active, telle que décrite dans `bot-engine/config.json`. Lecture
libre : l'application l'affiche pour vérification.

## `POST /config`

Remplace la configuration à chaud. Protégé par l'en-tête `X-Engine-Key`, dont la
valeur doit égaler `AUTOPLAY_ADMIN_KEY` côté serveur. Sans variable définie, toute
écriture est refusée (403) : le moteur reste en lecture seule.

```bash
curl -s -X POST https://votre-moteur/config \
  -H "X-Engine-Key: $AUTOPLAY_ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  --data @bot-engine/config.json
```

Si `AUTOPLAY_CONFIG` pointe vers un chemin inscriptible, la nouvelle configuration
y est également enregistrée.

## `POST /config/reload`

Recharge `config.json` depuis le disque — utile après un déploiement qui a changé
le fichier sans redémarrer le processus. Même protection par `X-Engine-Key`, mais
la clé peut être vide : dans ce cas l'appel est libre.

## `GET /docs`

Documentation OpenAPI générée par FastAPI, essayable depuis le navigateur.

---

## Séquence complète d'un coup

```
t = 0 ms      AccessibilityService.takeScreenshot()          (téléphone)
t ≈ 90 ms     réduction 1080 → 540 px, encodage JPEG q78      (téléphone)
t ≈ 120 ms    POST /analyze, 61 Ko                            (réseau)
t ≈ 160 ms    décodage + masques + Lab                        (moteur)
t ≈ 260 ms    localisation de la grille, extension            (moteur)
t ≈ 290 ms    palette, classification des 81 cases            (moteur)
t ≈ 320 ms    pré-classement puis Monte-Carlo                 (moteur)
t ≈ 340 ms    réponse JSON, ≈ 1 Ko                            (réseau)
t ≈ 420 ms    dispatchGesture, glissement de 180 ms           (téléphone)
t ≈ 600 ms    fin du geste
t ≈ 1850 ms   attente de settle_ms = 1250 ms, puis nouveau cycle
```

Les durées réseau dépendent de la liaison ; les durées moteur sont celles mesurées
en mode `balanced` sur un conteneur 2 vCPU.
