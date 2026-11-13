# AutoPlay — pilote automatique pour Homescapes

![Moteur — tests](https://github.com/Zynox7K/Bot-homesacape/actions/workflows/engine-tests.yml/badge.svg)
![Android — APK](https://github.com/Zynox7K/Bot-homesacape/actions/workflows/android-build.yml/badge.svg)

AutoPlay joue à votre place : une bulle flottante sur l'écran du jeu, un appui, et
le pilote enchaîne les meilleurs coups jusqu'à la fin du niveau.

Le principe directeur est simple : **le téléphone ne calcule rien**. Il photographie
l'écran, envoie l'image à votre moteur distant, reçoit des coordonnées de geste et
les rejoue. Ni OpenCV, ni solveur, ni dépendance native dans l'APK : le jeu reste
fluide, même sur un téléphone d'entrée de gamme.

```
┌─────────────────────────────┐          ┌──────────────────────────────┐
│  Téléphone (Android 11+)    │          │  Moteur (Docker, cloud)      │
│                             │  JPEG    │                              │
│  AccessibilityService       │─────────▶│  FastAPI  POST /analyze      │
│   · capture d'écran         │  ~120 Ko │   · décodage + réduction     │
│   · dispatchGesture         │          │   · détection de la grille   │
│  FloatingOverlayService     │◀─────────│   · classification des cases │
│   · bulle lecture / pause   │  JSON    │   · solveur match-3          │
│  MainActivity (Compose)     │  ~1 Ko   │   · geste normalisé 0–1      │
└─────────────────────────────┘          └──────────────────────────────┘
            ▲                                        ▲
            │                                        │ git push
            └────────────── Dépôt GitHub ────────────┘
                    bot-engine/config.json
```

Un aller-retour complet (capture, réseau, vision, résolution, geste) tient en
**300 à 600 ms** en mode `balanced`, mesuré par les tests d'intégration.

---

## Sommaire

1. [Ce que contient le dépôt](#ce-que-contient-le-dépôt)
2. [Installer l'application sur le téléphone](#installer-lapplication-sur-le-téléphone)
3. [Déployer le moteur](#déployer-le-moteur)
4. [Appairer l'application et le dépôt](#appairer-lapplication-et-le-dépôt)
5. [Jouer](#jouer)
6. [Réglages](#réglages)
7. [Mise au point](#mise-au-point)
8. [Documentation](#documentation)
9. [Avertissement](#avertissement)

---

## Ce que contient le dépôt

```
android-app/          Application Android : Kotlin, Jetpack Compose, SDK 34, minSdk 30
  app/src/main/java/dev/zynox/autoplay/
    MainActivity.kt              Écran de contrôle (appairage, autorisations, réglages)
    FloatingOverlayService.kt    Service de premier plan : bulle + boucle de pilotage
    TouchAccessibilityService.kt Capture d'écran et gestes système
    ApiClient.kt                 Client HTTP du moteur (HttpURLConnection + org.json)
    RuntimeBus.kt                État partagé service ↔ interface
    SettingsRepository.kt        Préférences persistantes
    ConfigSync.kt                Synchronisation de la configuration depuis GitHub
    overlay/BubbleView.kt        Bulle flottante dessinée à la main
    ui/                          Écran Compose, thème, composants, autorisations

bot-engine/           Moteur Python : FastAPI, OpenCV, solveur match-3
  engine/vision/      frame, grid, classifier, templates, debug
  engine/solver/      board, matching, simulate, search
  engine/api/         API HTTP
  engine/config.py    Configuration versionnée (config.json)
  tests/              119 tests (unitaires, vision, pipeline complet, API)
  tools/              analyze_file.py, capture_template.py
  templates/          Gabarits PNG des bonus (versionnés)
  Dockerfile          Image de déploiement

.github/workflows/    CI : tests du moteur, construction et publication de l'APK
docs/                 Architecture, protocole, déploiement, Android, mise au point
render.yaml           Déploiement Render en un clic
```

---

## Installer l'application sur le téléphone

### Option A — APK construit par GitHub Actions (recommandé)

Aucun outil à installer chez vous : le dépôt compile l'APK à chaque poussée.

1. Ouvrez **Actions → Android — APK** et cliquez sur la dernière exécution
   réussie : <https://github.com/Zynox7K/Bot-homesacape/actions/workflows/android-build.yml>
2. Dans **Artifacts**, téléchargez `AutoPlay-release-apk` et décompressez-le :
   vous obtenez `app-release.apk`.
3. Transférez l'APK sur le téléphone (câble, Drive, e-mail…) et ouvrez-le.
4. Android affiche « Ce type de fichier peut endommager votre appareil » :
   acceptez **Installer quand même** (l'APK est signé avec une clé de debug, il
   n'est pas passé par le Play Store).

Publier une étiquette `v1.0.1` crée en plus une **GitHub Release** avec l'APK
directement téléchargeable depuis le téléphone :

```bash
git tag v1.0.1 && git push origin v1.0.1
```

### Option B — Android Studio

1. **File → Open** et sélectionnez le dossier `android-app/`.
2. Attendez la synchronisation Gradle (le wrapper télécharge Gradle 8.7).
3. **Build → Build App Bundle(s) / APK(s) → Build APK(s)**.
4. L'APK est dans `android-app/app/build/outputs/apk/release/app-release.apk`.

### Option C — ligne de commande

```bash
cd android-app
./gradlew assembleRelease          # JDK 17 requis, ANDROID_HOME défini
ls app/build/outputs/apk/release/
```

---

## Déployer le moteur

Le moteur est une application Docker standard : n'importe quel hôte gratuit
convient. Trois options, de la plus simple à la plus libre.

### Render (un clic)

1. Poussez ce dépôt sur votre compte GitHub (c'est déjà le cas).
2. Sur Render : **New + → Blueprint** → sélectionnez le dépôt.
   Le fichier `render.yaml` à la racine décrit le service : image Docker
   construite depuis `bot-engine/Dockerfile`, sonde de vie sur `/health`,
   région Francfort, offre gratuite.
3. Renseignez `AUTOPLAY_ADMIN_KEY` (une phrase secrète de votre choix) dans
   l'onglet **Environment** du service — elle autorise l'application à écrire la
   configuration à chaud. Sans elle, le moteur reste en lecture seule.
4. Copiez l'URL publique, par exemple `https://autoplay-engine.onrender.com`.

### Railway / Fly.io / tout hôte Docker

```bash
cd bot-engine
docker build -t autoplay-engine .
docker run --rm -p 8080:8080 \
  -e AUTOPLAY_ADMIN_KEY="ma-cle-secrete" \
  autoplay-engine
curl http://localhost:8080/health
```

Variables d'environnement reconnues :

| Variable              | Rôle                                                        | Défaut        |
| --------------------- | ----------------------------------------------------------- | ------------- |
| `PORT`                | Port d'écoute                                               | `8080`        |
| `WEB_CONCURRENCY`     | Nombre de processus uvicorn                                 | `1`           |
| `AUTOPLAY_CONFIG`     | Chemin du `config.json` chargé au démarrage                 | `config.json` |
| `AUTOPLAY_ADMIN_KEY`  | Clé requise pour `POST /config` (vide = écriture refusée)   | *(vide)*      |

### Vérifier le déploiement

```bash
curl https://votre-moteur.onrender.com/health
# {"status":"ok","version":"1.0.0","uptime_s":12.3,"templates":[],"solver_mode":"expert"}

python bot-engine/tools/analyze_file.py capture.png --debug annotated.png
```

---

## Appairer l'application et le dépôt

Ouvrez AutoPlay et remplissez les trois cartes, dans l'ordre.

**1. Moteur de calcul** — collez l'URL du service, puis **Tester la connexion**.
La ligne verte affiche la version, le mode de calcul et la latence aller-retour.

**2. Dépôt GitHub** — c'est ce qui relie l'application au code :

| Champ                     | Valeur                                                            |
| ------------------------- | ----------------------------------------------------------------- |
| Dépôt                     | `https://github.com/Zynox7K/Bot-homesacape` (ou `Zynox7K/Bot-homesacape`) |
| Jeton d'accès             | vide pour un dépôt public ; PAT en lecture `Contents` pour un dépôt privé |
| Clé d'administration      | la valeur de `AUTOPLAY_ADMIN_KEY` sur le serveur                  |

Appuyez sur **Synchroniser la configuration**. L'application récupère
`bot-engine/config.json` via l'API GitHub, l'applique sur le moteur si la clé est
renseignée, et la conserve localement pour la renvoyer à chaque analyse.

Créer un jeton : GitHub → **Settings → Developer settings → Personal access
tokens → Fine-grained tokens** → dépôt cible → permission *Contents : Read-only*.

**3. Autorisations** — trois lignes à passer au vert :

| Autorisation                                  | Pourquoi                                             |
| --------------------------------------------- | ---------------------------------------------------- |
| Service d'accessibilité                       | Capturer l'écran et exécuter les gestes à votre place |
| Affichage par-dessus d'autres applications     | La bulle flottante visible pendant le jeu            |
| Notifications                                 | Le service de premier plan et ses commandes          |

Dans les réglages d'accessibilité, ouvrez **Services installés → AutoPlay** et
activez-le. Android affiche un avertissement générique : le service ne lit aucun
texte, il prend des captures et rejoue des glissements.

---

## Jouer

1. Lancez Homescapes et ouvrez un niveau.
2. Dans AutoPlay, **Démarrer l'assistant** : la bulle apparaît sur le jeu.
3. Déplacez la bulle là où elle ne gêne pas (glisser), puis **appuyez dessus**.
4. Le pilote joue. Appui = pause/reprise, appui long = retour aux réglages.

La notification permanente reprend les mêmes commandes. Le pilote se met en pause
tout seul si vous quittez le jeu, et s'arrête après cinq échecs consécutifs — il
ne tapera jamais à l'aveugle sur une autre application.

---

## Réglages

Tout se règle depuis l'application **et** depuis le dépôt ; le dépôt gagne, car il
est versionné.

| Réglage                        | Effet                                                            | Défaut   |
| ------------------------------ | ---------------------------------------------------------------- | -------- |
| Profondeur de calcul           | `fast` (8 candidats, 1 tirage), `balanced` (14/6), `expert` (24/16) | `expert` |
| Intervalle entre deux captures | Android limite la capture d'écran à une par seconde              | 1 200 ms |
| Qualité de la capture          | Plus elle baisse, plus l'envoi est rapide                        | 78 %     |
| Largeur d'analyse              | L'image est réduite à cette largeur avant envoi                  | 540 px   |
| Durée des gestes               | Multiplicateur appliqué à la durée demandée par le moteur        | ×1,0     |
| Délai maximal d'une analyse    | Au-delà, le tour est considéré comme perdu                       | 8 s      |
| Couleur prioritaire            | Indice dans la palette détectée ; utile pour les objectifs       | auto     |
| Application surveillée         | Paquet du jeu (`com.playrix.homescapes`)                         | —        |
| Pause hors du jeu              | Interrompt le pilote quand le jeu passe au second plan           | activé   |
| Analyse seule                  | Le moteur répond, aucun geste n'est exécuté                      | désactivé |

Pour changer durablement un réglage, modifiez `bot-engine/config.json`, poussez,
puis **Synchroniser la configuration** dans l'application (ou laissez
`AUTOPLAY_ADMIN_KEY` faire le travail au déploiement).

---

## Mise au point

```bash
cd bot-engine
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests                      # 119 tests
python tools/analyze_file.py capture.png --show-board --debug annotated.png
python tools/capture_template.py capture.png --row 4 --col 2 --kind bomb
```

Récupérer une capture du téléphone : `adb exec-out screencap -p > capture.png`.

Les bonus reconnus par gabarit (fusées, avion, bombe, disco) sont bien plus fiables
que l'heuristique colorimétrique : voir [`docs/mise-au-point.md`](docs/mise-au-point.md).

---

## Documentation

| Document                                   | Contenu                                                     |
| ------------------------------------------ | ----------------------------------------------------------- |
| [`docs/architecture.md`](docs/architecture.md)   | Découpage, fils d'exécution, budget de latence, vision, solveur |
| [`docs/protocole.md`](docs/protocole.md)         | Référence complète de l'API et du format des réponses        |
| [`docs/deploiement.md`](docs/deploiement.md)     | Render, Railway, Docker, variables, synchronisation GitHub   |
| [`docs/android.md`](docs/android.md)             | Construction de l'APK, autorisations, fonctionnement du pilote |
| [`docs/mise-au-point.md`](docs/mise-au-point.md) | Outils CLI, tests, gabarits de bonus, réglages de la vision   |

---

## Avertissement

AutoPlay est un projet personnel d'automatisation, fourni tel quel, sans lien avec
Playrix ni avec Google. Il enfreint les conditions d'utilisation de la plupart des
jeux mobiles : votre compte peut être suspendu. Utilisez-le en connaissance de
cause, sur un compte auquel vous tenez peu, et gardez la main sur la bulle —
l'appui long ramène immédiatement aux réglages, la notification arrête le service.
