package dev.zynox.autoplay.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.ContextCompat
import dev.zynox.autoplay.TouchAccessibilityService

/**
 * État des trois autorisations nécessaires au pilote.
 *
 * Aucune n'est accordable par une simple boîte de dialogue : l'accessibilité et
 * la superposition se règlent dans les écrans système, d'où les intentions
 * ci-dessous. La notification, elle, se demande à l'exécution depuis Android 13.
 */
internal data class PermissionStatus(
    val accessibility: Boolean,
    val overlay: Boolean,
    val notifications: Boolean,
) {
    /** Le pilote peut fonctionner : superposition et accessibilité sont actives. */
    val canPilot: Boolean
        get() = accessibility && overlay

    companion object {
        fun read(context: Context): PermissionStatus {
            val notifications = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                ContextCompat.checkSelfPermission(
                    context,
                    Manifest.permission.POST_NOTIFICATIONS,
                ) == PackageManager.PERMISSION_GRANTED
            } else {
                true
            }
            return PermissionStatus(
                accessibility = TouchAccessibilityService.isConnected,
                overlay = Settings.canDrawOverlays(context),
                notifications = notifications,
            )
        }
    }
}

/** Ouvre les réglages d'accessibilité, où l'utilisateur active le service AutoPlay. */
internal fun Context.openAccessibilitySettings() {
    startActivity(
        Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
    )
}

/** Ouvre l'écran « Affichage par-dessus d'autres applications » pour ce paquet. */
internal fun Context.openOverlaySettings() {
    startActivity(
        Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.parse("package:$packageName"),
        ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
    )
}

/** Ouvre les réglages de notification de l'application. */
internal fun Context.openNotificationSettings() {
    startActivity(
        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
    )
}
