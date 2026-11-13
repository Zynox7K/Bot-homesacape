package dev.zynox.autoplay.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** Thème sombre unique de l'application. */
@Composable
fun AutoPlayTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = BrandGreen,
            onPrimary = Color(0xFF06130B),
            primaryContainer = Color(0xFF143522),
            onPrimaryContainer = BrandGreen,
            secondary = AccentBlue,
            onSecondary = Color(0xFF06131F),
            secondaryContainer = Color(0xFF14253A),
            onSecondaryContainer = AccentBlue,
            tertiary = WarningOrange,
            onTertiary = Color(0xFF1F1405),
            background = DarkBackground,
            onBackground = TextPrimary,
            surface = DarkSurface,
            onSurface = TextPrimary,
            surfaceVariant = DarkCard,
            onSurfaceVariant = TextSecondary,
            outline = DarkStroke,
            outlineVariant = DarkStroke,
            error = DangerRed,
            onError = Color(0xFF2A0A0A),
            errorContainer = Color(0xFF3A1414),
            onErrorContainer = DangerRed,
        ),
        content = content,
    )
}
