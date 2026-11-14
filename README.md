# Bot local Homescapes — reconnaissance automatique expérimentale

Application Android 11+ sans serveur et sans permission Internet. Elle analyse les captures sur le téléphone, repère certaines pièces colorées et tente des échanges match-3. Aucune configuration manuelle de la grille n'est demandée.

## Ce qui fonctionne et ce qui ne fonctionne pas encore

- La détection a été développée et testée automatiquement sur **une capture fournie par l'utilisateur**, avec une grille irrégulière. L'application recherche un plateau stable sur deux captures successives et ne tente pas de jouer le même état deux fois.
- La reconnaissance dépend fortement des couleurs, de l'échelle, de la position et du rendu du jeu. Elle ne reconnaît pas toutes les formes de grille, pièces, bonus, animations ou obstacles ; elle peut se tromper ou rester immobile. Le solveur n'optimise pas les objectifs spécifiques d'un niveau et ne garantit pas de le gagner. Aucun essai sur téléphone réel n'a été effectué ici.
- **Le passage au niveau suivant n'est pas encore automatisé.** En l'absence de capture vérifiée de l'écran de victoire, le bot ne clique pas au hasard sur les dialogues ou les boutons : ceux-ci pourraient dépenser des pièces ou déclencher un achat. Il se met en pause quand il ne reconnaît pas de plateau ou de coup pendant plusieurs cycles.
- La capture et l'analyse consomment des ressources ; le zéro ralentissement ne peut pas être garanti. Le bot attend 2,5 secondes entre les cycles.
- Vérifiez les conditions d'utilisation du jeu ; l'automatisation peut entraîner des restrictions de compte. Ne laissez pas le bot jouer sans surveillance.

## Installation de l'APK

Sur cette branche, après une compilation GitHub Actions réussie, téléchargez `apk/bot-local.apk`, copiez-le sur le téléphone et touchez-le pour lancer l'installation. Android exige une confirmation ; l'installation n'est pas silencieuse. C'est un APK debug : Play Protect peut émettre un avertissement et Android peut restreindre l'activation de l'accessibilité.

**Mise à jour de l'ancienne version :** l'APK généré par GitHub Actions utilise une clé debug propre à l'environnement de compilation. Si Android indique que la signature ne correspond pas, désinstallez d'abord l'ancienne version, puis installez la nouvelle et réactivez les permissions. Cette désinstallation efface les préférences de l'application, mais pas les données de Homescapes.

## Utilisation

1. Ouvrez **Bot local**, activez l'accessibilité et autorisez la superposition via les boutons. Si Android signale des paramètres restreints, l'option **Autoriser les paramètres restreints** peut se trouver dans les informations de l'application, menu ⋮. Accordez l'accessibilité uniquement si vous faites confiance à l'APK : elle permet de lire la fenêtre active et de simuler des gestes.
2. Appuyez sur **Afficher la bulle**, ouvrez Homescapes et un plateau stable, puis touchez **▶**. Aucun calibrage n'est requis. L'analyse attend deux captures cohérentes avant de tenter un coup. La bulle affiche **Ⅱ** pendant la lecture ; touchez-la pour mettre en pause.
3. Surveillez les premiers coups. La détection ne reconnaît pas tous les obstacles ; arrêtez le bot s'il se trompe. S'il s'arrête avec « Plateau ou coup non reconnu », ne le relancez pas sans vérifier l'écran.
4. Utilisez **Fermer la bulle** dans l'application ou l'action **Arrêter** de sa notification. Un geste déjà envoyé au système peut se terminer après la pause. Désactivez les permissions dans les paramètres Android lorsqu'elles ne sont plus nécessaires.

Le service vérifie que l'application active est Homescapes avant capture et geste. Les captures restent en mémoire et ne sont pas envoyées sur Internet. Une transition peut toutefois apparaître dans une capture ; ne laissez pas le bot actif sur un écran sensible.

## Construire et tester

Ouvrez `android-app/` dans Android Studio avec JDK 17 et le SDK Android 35, ou exécutez :

```bash
cd android-app
./gradlew testDebugUnitTest assembleDebug
# Windows : gradlew.bat testDebugUnitTest assembleDebug
```

L'APK est produit dans `android-app/app/build/outputs/apk/debug/app-debug.apk`. Le workflow `.github/workflows/ci.yml` exécute les tests et construit l'APK sur GitHub Actions. Il publie l'APK sur la branche `arena/f301f018-bot-homesacape` après une exécution réussie. Les tests comprennent une capture réelle du plateau et des données synthétiques ; ils ne remplacent pas un test sur appareil.
