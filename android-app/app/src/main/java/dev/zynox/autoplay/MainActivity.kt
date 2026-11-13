package dev.zynox.autoplay

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import dev.zynox.autoplay.ui.AutoPlayTheme
import dev.zynox.autoplay.ui.HomeScreen

/**
 * Écran de contrôle de l'application.
 *
 * Son rôle se limite à l'appairage et au pilotage : renseigner l'adresse du
 * moteur, synchroniser la configuration du dépôt GitHub, vérifier les trois
 * autorisations, puis démarrer ou arrêter [FloatingOverlayService]. Aucune
 * analyse n'y est faite : le jeu reste fluide même lorsque cet écran est ouvert.
 *
 * L'état des autorisations est relu en continu par l'écran Compose, ce qui rend
 * le retour des réglages système immédiatement visible.
 */
class MainActivity : ComponentActivity() {

    private lateinit var settings: SettingsRepository

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = SettingsRepository(this)
        setContent {
            AutoPlayTheme {
                HomeScreen(settings = settings)
            }
        }
    }
}
