# Déploiement du moteur

Le moteur est une application Docker sans état : une image, un port, quatre
variables d'environnement. Aucun base de données, aucun volume obligatoire.

---

## 1. Image Docker

`bot-engine/Dockerfile` :

```dockerfile
FROM python:3.12-slim
ENV AUTOPLAY_CONFIG=/app/config.json
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY engine ./engine
COPY config.json ./
COPY templates ./templates
EXPOSE 8080
HEALTHCHECK ... CMD ... urlopen('http://127.0.0.1:8080/health')
CMD uvicorn engine.api.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers ${WEB_CONCURRENCY:-1}
```

`opencv-python-headless` évite toute dépendance à X11 ; `libglib2.0-0` est la
seule bibliothèque système requise. L'image finale pèse environ 250 Mo.

### Construction et essai local

```bash
cd bot-engine
docker build -t autoplay-engine .
docker run --rm -p 8080:8080 -e AUTOPLAY_ADMIN_KEY="ma-cle-secrete" autoplay-engine

# dans un autre terminal
curl http://localhost:8080/health
curl -X POST http://localhost:8080/analyze -F "image=@capture.png"
```

### Variables d'environnement

| Variable             | Rôle                                                      | Défaut        |
| -------------------- | --------------------------------------------------------- | ------------- |
| `PORT`               | Port d'écoute (Render et Railway l'imposent)              | `8080`        |
| `WEB_CONCURRENCY`    | Processus uvicorn ; 1 par vCPU est un bon point de départ  | `1`           |
| `AUTOPLAY_CONFIG`    | `config.json` chargé au démarrage et réécrit par `POST /config` | `/app/config.json` |
| `AUTOPLAY_ADMIN_KEY` | Clé exigée par `POST /config` ; vide = écriture refusée    | *(vide)*      |

Choisissez `AUTOPLAY_ADMIN_KEY` comme un mot de passe long : c'est elle qui permet
de modifier le comportement du bot à distance.

---

## 2. Render (déploiement en un clic)

Le fichier `render.yaml` à la racine décrit le service. Render ne cherche ce
fichier qu'à la racine du dépôt, d'où son emplacement.

1. GitHub : votre dépôt `Bot-homesacape` (déjà fait si vous lisez ceci en ligne).
2. Render → **New +** → **Blueprint** → choisissez le dépôt → **Apply**.
3. Render construit l'image depuis `bot-engine/Dockerfile`, démarre le service et
   sonde `/health` toutes les 30 s.
4. Onglet **Environment** : ajoutez `AUTOPLAY_ADMIN_KEY` avec votre clé, puis
   **Save changes** (le service redémarre).
5. Copiez l'URL : `https://autoplay-engine.onrender.com`.

Offre gratuite : le service s'endort après 15 minutes d'inactivité et met 30 à 60 s
à se réveiller. Lancez une analyse de temps en temps, ou passez sur une offre
payante, si vous voulez une reprise instantanée. Région configurée : Francfort.

---

## 3. Railway

```bash
railway init                       # ou créer un projet depuis le dépôt GitHub
railway up --service autoplay-engine
railway variables set AUTOPLAY_ADMIN_KEY="ma-cle-secrete" WEB_CONCURRENCY=1
railway domain                     # URL publique
```

Railway détecte `bot-engine/Dockerfile` ; indiquez ce chemin comme **Dockerfile
path** et `bot-engine` comme **contexte** dans les réglages du service, exactement
comme dans `render.yaml`.

---

## 4. Fly.io, VPS, ou tout autre hôte

```bash
fly launch --image autoplay-engine --region cdg --no-deploy
fly secrets set AUTOPLAY_ADMIN_KEY="ma-cle-secrete"
fly deploy
```

Sur un VPS, reverse-proxy conseillé (Caddy, nginx) pour servir en HTTPS :
l'application Android accepte le HTTP simple (`usesCleartextTraffic`), mais une
liaison non chiffrée expose vos captures d'écran et votre clé d'administration.

```
# /etc/caddy/Caddyfile
autoplay.exemple.com {
    reverse_proxy 127.0.0.1:8080
}
```

---

## 5. Synchronisation avec le dépôt

Trois mécanismes, du plus automatique au plus manuel.

**Au déploiement.** L'image embarque `config.json` tel qu'il est dans le dépôt :
chaque `git push` suivi d'un redéploiement applique les nouveaux réglages. Render
et Railway redéploient automatiquement sur poussée de `main` (`autoDeploy: true`).

**À chaud, depuis l'application.** La carte *Dépôt GitHub* récupère
`bot-engine/config.json` via l'API GitHub (`Accept: application/vnd.github.raw+json`)
puis le pousse sur `POST /config` avec l'en-tête `X-Engine-Key`. Réglages appliqués
en moins de deux secondes, sans redémarrer le service.

**À chaud, en ligne de commande.**

```bash
curl -X POST https://votre-moteur/config \
  -H "X-Engine-Key: $AUTOPLAY_ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  --data @bot-engine/config.json
```

Même sans clé d'administration, la configuration récupérée du dépôt est renvoyée
dans le champ `config` de chaque `POST /analyze` : le téléphone joue avec les
réglages versionnés, et le serveur garde sa configuration d'origine intacte.

### Recharger depuis le disque

```bash
curl -X POST https://votre-moteur/config/reload -H "X-Engine-Key: $AUTOPLAY_ADMIN_KEY"
```

Utile quand vous modifiez le fichier monté dans le conteneur sans redémarrer.

---

## 6. Dimensionnement

| Charge                        | Recommandation                                    |
| ----------------------------- | ------------------------------------------------- |
| 1 joueur                      | offre gratuite, 1 vCPU partagé, 512 Mo            |
| 2 à 5 joueurs simultanés      | 2 vCPU dédiés, `WEB_CONCURRENCY=2`                |
| Niveaux difficiles en `expert` | compter 200 à 400 ms de solveur par coup          |

Une analyse est essentiellement du calcul CPU (OpenCV puis Monte-Carlo). Chaque
processus uvicorn traite une analyse à la fois dans son pool de threads : alignez
`WEB_CONCURRENCY` sur le nombre de vCPU. La mémoire reste sous 300 Mo par
processus pour des captures de 1080 × 2400.

Le mode `expert` coûte environ deux fois plus de temps solveur que `balanced`. Si
votre offre gratuite sature, passez l'application en `balanced` : la différence de
qualité de jeu est faible sur les niveaux courants et nulle sur les plateaux
évidents.

---

## 7. Vérification après déploiement

```bash
curl -s https://votre-moteur/health | python3 -m json.tool
curl -s https://votre-moteur/config  | python3 -m json.tool | head -20

# analyse réelle contre le service déployé, exactement comme le téléphone
cd bot-engine
python tools/analyze_file.py capture.png --url https://votre-moteur --mode expert
python tools/analyze_file.py capture.png --url https://votre-moteur --debug /tmp/annotated.png
```

`--url` envoie la capture en `multipart/form-data` au service distant et affiche la
réponse : état, grille, coup retenu, minutage serveur. Sans `--url`, la même
commande exécute le moteur en local, ce qui permet de comparer un déploiement au
code du dépôt. L'image annotée trace la grille détectée, l'identification de chaque
case et le coup retenu : c'est le moyen le plus rapide de valider un déploiement sur
de vraies captures de votre téléphone.
