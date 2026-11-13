# Bot local pour Homescapes — Android

Une application Android native légère, **sans serveur, sans compte GitHub et sans permission Internet**. L'application capture l'écran, analyse un petit échantillon de pixels sur chaque case et effectue un glissement avec le service d'accessibilité. La reconnaissance et les calculs sont effectués sur le téléphone, sans OpenCV.

## Avant de commencer

- Android **11 ou supérieur** et la version de Homescapes dont l'identifiant est `com.playrix.homescapes`.
- La capture, l'analyse et les gestes ont un coût : le **zéro ralentissement n'est pas garanti**. L'application attend 2,5 secondes entre deux cycles et ne traite qu'une capture à la fois.
- Le solveur est volontairement simple : il cherche un alignement de trois pièces et ne comprend ni les objectifs, ni les bonus, ni tous les obstacles. Une case bloquée peut être prise pour une pièce, surtout si elle a une couleur similaire. Un plateau irrégulier ou de nouvelles animations peuvent empêcher la reconnaissance. **Surveillez toujours le bot** et mettez-le en pause en cas d'erreur. Le comportement sur un appareil et sur un plateau réels doit être vérifié ; il n'est pas garanti pour tous les niveaux.
- Vérifiez les conditions d'utilisation du jeu : l'automatisation peut entraîner des restrictions de compte.

## Télécharger et installer l'application

Si un fichier APK est fourni avec le dépôt, copiez-le sur le téléphone et touchez-le pour lancer son installation. Android vous demandera de confirmer et pourra demander l'autorisation d'installer des applications depuis votre navigateur ou gestionnaire de fichiers : l'installation ne peut pas être entièrement automatique. Ne téléchargez un APK que depuis une source de confiance.

Sinon, après avoir poussé les fichiers sur GitHub, allez dans **Actions > Construire l'APK Android**, ouvrez une exécution réussie et téléchargez l'artefact **bot-local-android-apk**. Décompressez-le pour obtenir `app-debug.apk`. Il peut être nécessaire de vous connecter à GitHub pour télécharger un artefact. C'est un APK **debug** signé par la clé de développement du système de compilation ; un autre APK portant le même identifiant mais signé avec une autre clé ne pourra pas être mis à jour directement sans le désinstaller.

### Construire soi-même

Ouvrez `android-app/` dans Android Studio avec JDK 17 et le SDK Android 35, puis utilisez **Build > Build APK(s)**. Ou lancez :

```bash
cd android-app
./gradlew testDebugUnitTest assembleDebug
# Windows : gradlew.bat testDebugUnitTest assembleDebug
```

Le résultat est `android-app/app/build/outputs/apk/debug/app-debug.apk`. La construction nécessite l'accès aux dépôts Gradle et Google. Les tests automatisés couvrent le solveur et la classification sur des données synthétiques, pas l'expérience de jeu réelle.

## Activer et calibrer

1. Ouvrez **Bot local**. Appuyez sur **Activer l'accessibilité**, sélectionnez le service du bot et activez-le. Puis revenez et appuyez sur **Autoriser la superposition**. Certains appareils Android bloquent l'accessibilité pour les applications installées par APK : dans les informations de l'application, utilisez le menu à trois points puis **Autoriser les paramètres restreints**, si cette option est proposée.
2. Appuyez sur **Afficher la bulle**. Ouvrez Homescapes et un plateau **stable et rectangulaire**. Vous pouvez déplacer la bulle en la faisant glisser.
3. Faites un **appui long** sur la bulle pour ouvrir le calibrage. La capture reste uniquement en mémoire sur votre téléphone. Choisissez le nombre de lignes et colonnes, touchez le **centre** de la case en haut à gauche puis celui de la case en bas à droite. Vérifiez que les traits correspondent à la grille.
4. Touchez une pièce représentative de **chaque couleur différente**, au moins trois. Chaque couleur reçoit un numéro. Vérifiez sur toute la grille que chaque couleur porte le même numéro et que les cases incertaines sont marquées `?`. Si ce n'est pas correct, appuyez sur **Recommencer** et essayez une autre capture stable. Si la reconnaissance reste fausse, n'utilisez pas le bot sur ce plateau.
5. Appuyez sur **Enregistrer** pour revenir au jeu. Touchez brièvement la bulle pour **démarrer ou mettre en pause**. Recalibrez chaque fois que la grille, sa taille ou son apparence change. Si l'écran change de résolution ou d'orientation, le bot s'arrête et demande un nouveau calibrage.
6. Utilisez **Fermer la bulle** dans l'application pour arrêter le service, ou son action **Arrêter** dans la notification si Android l'affiche. Pour désactiver complètement l'accès, retirez les deux permissions dans les paramètres Android.

L'application vérifie que le jeu est actif avant de capturer et avant de jouer. Pendant une pause, aucun nouveau geste n'est envoyé ; un geste déjà transmis à Android peut toutefois se terminer. Une capture peut contenir une transition d'écran et l'accessibilité donne accès au contenu de la fenêtre active : ne laissez pas le bot actif sur un écran sensible.

## Structure

- `android-app/` : code Kotlin, interface Compose, calibrage, solveur local et tests.
- `.github/workflows/ci.yml` : tests et construction de l'APK sur GitHub Actions. Aucun déploiement de serveur n'est nécessaire.
