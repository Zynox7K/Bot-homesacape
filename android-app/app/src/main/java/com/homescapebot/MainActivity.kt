package com.homescapebot

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            var message by remember { mutableStateOf("") }
            var refresh by remember { mutableIntStateOf(0) }
            val owner = LocalLifecycleOwner.current
            DisposableEffect(owner) {
                val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) refresh++ }
                owner.lifecycle.addObserver(observer)
                onDispose { owner.lifecycle.removeObserver(observer) }
            }
            @Suppress("UNUSED_VARIABLE") val currentRefresh = refresh
            MaterialTheme {
                Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("Bot local", style = MaterialTheme.typography.headlineMedium)
                    Text("Aucun serveur ni connexion Internet. Les captures et les calculs restent sur le téléphone.")
                    Text("1. Activez les deux permissions, puis affichez la bulle.\n2. Ouvrez un plateau stable de Homescapes.\n3. Touchez la bulle pour démarrer ou mettre en pause. Aucun calibrage manuel n'est nécessaire.")
                    Button(onClick = { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }) {
                        Text(if (TouchAccessibilityService.instance != null) "Accessibilité activée ✓" else "Activer l'accessibilité")
                    }
                    Button(onClick = { startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))) }) {
                        Text(if (Settings.canDrawOverlays(this@MainActivity)) "Superposition activée ✓" else "Autoriser la superposition")
                    }
                    Button(onClick = {
                        when {
                            TouchAccessibilityService.instance == null -> message = "Activez le service d'accessibilité."
                            !Settings.canDrawOverlays(this@MainActivity) -> message = "Autorisez la superposition."
                            else -> {
                                ContextCompat.startForegroundService(this@MainActivity, Intent(this@MainActivity, FloatingOverlayService::class.java))
                                message = "Bulle affichée. Ouvrez le jeu puis touchez la bulle pour démarrer."
                            }
                        }
                    }) { Text("Afficher la bulle") }
                    OutlinedButton(onClick = {
                        stopService(Intent(this@MainActivity, FloatingOverlayService::class.java))
                        message = "Bulle arrêtée."
                    }) { Text("Fermer la bulle") }
                    Text("Surveillez toujours le bot : les obstacles et bonus peuvent être mal reconnus. La reconnaissance automatique ne fonctionne pas sur tous les niveaux.", style = MaterialTheme.typography.bodySmall)
                    if (message.isNotEmpty()) Text(message)
                }
            }
        }
    }
}
