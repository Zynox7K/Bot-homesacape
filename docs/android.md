# Application Android

Kotlin, Jetpack Compose, `compileSdk` 34, `minSdk` 30 (Android 11), JDK 17.
Paquet : `dev.zynox.autoplay`. Dépendances : Compose (BOM 2024.06.00), Material 3,
`activity-compose`, `core-ktx` et `kotlinx-coroutines-android`. **Aucune**
bibliothèque de vision, de réseau ou de JSON : `HttpURLConnection` et `org.json`
viennent du framework.

---

## 1. Obtenir l'APK

### Par GitHub Actions (sans rien installer)

Chaque poussée sur n'importe quelle branche déclenche le workflow
**Android — APK**, qui assemble `app-release.apk` signé avec une clé de debug et
le publie comme artefact.

1. <https://github.com/Zynox7K/Bot-homesacape/actions/workflows/android-build.yml>
2. Dernière exécution réussie → **Artifacts** → `AutoPlay-release-apk`.
3. Décompressez, copiez `app-release.apk` sur le téléphone, ouvrez-le,
   **Installer quand même**.

Une étiquette `v*` crée en plus une GitHub Release avec l'APK renommé
`AutoPlay-v1.0.0.apk`, téléchargeable directement depuis le navigateur du
téléphone :

```bash
git tag v1.0.0 && git push origin v1.0.0
```

### Par Android Studio

**File → Open** sur `android-app/`, attendre la synchronisation Gradle, puis
**Build → Build App Bundle(s) / APK(s) → Build APK(s)**. Le produit est dans
`app/build/outputs/apk/release/app-release.apk`.

### En ligne de commande

```bash
cd android-app
./gradlew assembleRelease
```

Prérequis : JDK 17 dans `PATH`, variable `ANDROID_HOME` (ou un fichier
`local.properties` contenant `sdk.dir=…`), plateforme `android-34` installée.
Le wrapper Gradle 8.7 est versionné, `gradle/wrapper/gradle-wrapper.jar` compris :
aucun Gradle à installer.

### Signature

La variante `release` est signée avec la clé de debug, afin que l'APK produit par
la CI soit installable immédiatement. Pour une publication sur le Play Store,
déclarez votre propre `signingConfig` dans `android-app/app/build.gradle.kts` et
pointez-le depuis le bloc `release` — ne versionnez jamais le keystore
(`*.jks` et `*.keystore` sont dans `.gitignore`).

---

## 2. Autorisations

| Autorisation                              | Usage                                                            | Écran système                        |
| ----------------------------------------- | ---------------------------------------------------------------- | ---------------------------------- |
| Service d'accessibilité                   | `takeScreenshot()` et `dispatchGesture()`                        | Accessibilité → Services installés    |
| `SYSTEM_ALERT_WINDOW`                     | bulle flottante par-dessus le jeu                                | Affichage par-dessus d'autres applis |
| `POST_NOTIFICATIONS` (Android 13+)        | notification du service de premier plan et ses commandes          | dialogue d'autorisation              |
| `INTERNET`, `ACCESS_NETWORK_STATE`        | appels au moteur                                                 | accordées à l'installation           |
| `FOREGROUND_SERVICE_SPECIAL_USE`          | maintenir le pilote actif écran éteint                            | accordée à l'installation            |
| `WAKE_LOCK`                               | verrou partiel pendant une session, expiration à 4 h              | accordée à l'installation            |

Chaque ligne de la carte **Autorisations** passe au vert dès que l'écran système
est validé : l'application les relit toutes les 700 ms, inutile de la relancer.

L'avertissement d'Android sur les services d'accessibilité est générique. Celui-ci
ne lit aucun texte, n'inspecte aucune fenêtre (`canRetrieveWindowContent="false"`)
et n'écoute que les changements de fenêtre au premier plan.

---

## 3. Fonctionnement du pilote

```
FloatingOverlayService (service de premier plan)
│
├── bulle BubbleView : appui = lecture/pause, appui long = réglages, glisser = déplacer
│
└── coroutine unique sur Dispatchers.Default
        │
        ├── le jeu est-il au premier plan ?            sinon : pause
        ├── AccessibilityService.takeScreenshot()      (capture, ~90 ms)
        ├── Bitmap 1080 px → 540 px, JPEG q78          Dispatchers.Default (~25 ms)
        ├── POST /analyze (61 Ko)                      Dispatchers.IO (~200 ms)
        ├── coordonnées 0-1 × taille écran → PointF
        ├── dispatchGesture(glissement, 180 ms)        callback sur le fil principal
        └── delay(max(intervalle, settle_ms))          (~1,25 s)
```

Points de conception à connaître avant de modifier le code :

* **Un seul service pilote.** `TouchAccessibilityService` expose des fonctions
  statiques (`capture`, `swipe`, `tap`, `screenSize`) ; `FloatingOverlayService`
  les appelle sans détenir de référence au service. Si l'accessibilité est coupée,
  ces fonctions renvoient `null`/`false` et le pilote signale l'erreur au lieu de
  planter.
* **Suspension bornée.** Chaque appel système est enveloppé dans
  `suspendCancellableCoroutine` puis dans `withTimeoutOrNull` : un callback perdu ne
  bloque jamais la boucle.
* **Mises à jour d'interface repostées.** La bulle vit sur le fil principal ; la
  boucle tourne sur `Default`. `syncBubble()` poste donc via un `Handler`, et ne
  redessine que si l'état a changé.
* **État partagé.** `RuntimeBus` expose un `StateFlow<RuntimeSnapshot>` et un
  journal borné à 80 lignes. L'écran Compose observe, le service écrit : aucune
  référence directe entre les deux.
* **Persistance.** `SettingsRepository` écrit immédiatement dans
  `autoplay_settings.xml` (les curseurs sont débouncés de 500 ms). Le fichier est
  couvert par les règles de sauvegarde du manifeste.
* **Capture limitée par le système.** Android autorise environ une capture par
  seconde. L'intervalle est borné à 1 000 ms côté application et le moteur renvoie
  son propre `capture_interval_ms` ; la boucle attend le maximum des deux et du
  `settle_ms` reçu.

---

## 4. Résolution des problèmes

| Symptôme                                     | Cause probable                                       | Remède                                                        |
| -------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------- |
| La bulle n'apparaît pas                      | superposition refusée                                | carte **Autorisations** → ligne 2 → **Autoriser**             |
| « Activez d'abord le service d'accessibilité » | service désactivé ou tué par le système             | réactivez-le ; sur Xiaomi/Huawei, désactivez l'optimisation batterie pour AutoPlay |
| « Moteur injoignable »                       | URL fausse, service endormi, HTTP bloqué             | **Tester la connexion** ; réveillez le service ; vérifiez le schéma |
| « Grille non reconnue »                      | niveau pas encore chargé, ou zoom du jeu             | attendez l'affichage complet du plateau ; voir `docs/mise-au-point.md` |
| Coups lents                                  | mode `expert` sur un petit serveur                   | passez en `balanced`, ou réduisez la largeur d'analyse à 420 px |
| Le pilote joue alors que vous avez quitté le jeu | paquet surveillé incorrect                        | réglage **Application surveillée**, ou désactivez la pause     |
| Capture refusée en boucle                    | intervalle inférieur à une seconde                   | remontez l'intervalle à 1 500 ms                              |
| L'APK ne s'installe pas                      | signature différente d'une version précédente        | désinstallez l'ancienne application, puis réinstallez          |

Le journal de l'écran de contrôle horodate chaque événement : c'est le premier
réflexe en cas de comportement inattendu.
