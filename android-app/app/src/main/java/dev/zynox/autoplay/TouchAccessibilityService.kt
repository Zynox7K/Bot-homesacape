package dev.zynox.autoplay

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.PointF
import android.os.Handler
import android.os.Looper
import android.view.Display
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent

/**
 * Point d'entrée système du pilote : capture d'écran et gestes.
 *
 * L'API 30 d'`AccessibilityService` suffit aux deux besoins du pilote :
 * [takeScreenshot] récupère l'image du jeu sans `MediaProjection` ni activité de
 * consentement, et [dispatchGesture] rejoue un glissement de façon indiscernable
 * d'un doigt. Aucun traitement d'image n'est fait ici : la capture part telle
 * quelle vers le moteur distant.
 *
 * Les événements d'accessibilité ne servent qu'à suivre l'application au premier
 * plan, afin de mettre le pilote en pause dès que l'utilisateur quitte le jeu.
 */
class TouchAccessibilityService : AccessibilityService() {

    private val mainHandler = Handler(Looper.getMainLooper())

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        RuntimeBus.log(getString(R.string.log_accessibility_connected), LogLevel.SUCCESS)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        val source = event ?: return
        if (source.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        val foreground = source.packageName?.toString().orEmpty()
        if (foreground.isEmpty() || foreground == packageName) return
        RuntimeBus.update { it.copy(foregroundPackage = foreground) }
    }

    override fun onInterrupt() {
        // Aucun retour sensoriel continu : rien à interrompre.
    }

    override fun onUnbind(intent: Intent?): Boolean {
        release()
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        release()
        super.onDestroy()
    }

    private fun release() {
        if (instance !== this) return
        instance = null
        RuntimeBus.log(getString(R.string.log_accessibility_disconnected), LogLevel.WARNING)
    }

    /** Taille réelle de l'écran, en pixels, ou `null` si indisponible. */
    fun screenSize(): PointF? {
        val manager = getSystemService(Context.WINDOW_SERVICE) as? WindowManager ?: return null
        val bounds = manager.currentWindowMetrics.bounds
        if (bounds.width() <= 0 || bounds.height() <= 0) return null
        return PointF(bounds.width().toFloat(), bounds.height().toFloat())
    }

    /**
     * Capture l'écran courant.
     *
     * Le système limite cette API à environ une capture par seconde : le pilote
     * respecte un intervalle minimal, configuré par défaut à 1,2 s.
     */
    fun captureScreen(onResult: (Bitmap?) -> Unit) {
        takeScreenshot(
            Display.DEFAULT_DISPLAY,
            mainExecutor,
            object : TakeScreenshotCallback {
                override fun onSuccess(screenshot: ScreenshotResult) {
                    val buffer = screenshot.hardwareBuffer
                    val hardware = Bitmap.wrapHardwareBuffer(buffer, screenshot.colorSpace)
                    val readable = hardware?.copy(Bitmap.Config.ARGB_8888, false)
                    hardware?.recycle()
                    buffer.close()
                    onResult(readable)
                }

                override fun onFailure(errorCode: Int) {
                    RuntimeBus.log(
                        getString(R.string.log_screenshot_failed, errorCode),
                        LogLevel.WARNING,
                    )
                    onResult(null)
                }
            },
        )
    }

    /** Glissement d'un point à un autre, exécuté par le système. */
    fun performSwipe(from: PointF, to: PointF, durationMs: Long, onResult: (Boolean) -> Unit): Boolean {
        val path = Path().apply {
            moveTo(from.x, from.y)
            lineTo(to.x, to.y)
        }
        return dispatch(path, durationMs, onResult)
    }

    /** Appui simple, utilisé pour les bonus déjà posés sur le plateau. */
    fun performTap(at: PointF, durationMs: Long, onResult: (Boolean) -> Unit): Boolean {
        val path = Path().apply {
            moveTo(at.x, at.y)
            lineTo(at.x + 1f, at.y)
        }
        return dispatch(path, durationMs, onResult)
    }

    private fun dispatch(path: Path, durationMs: Long, onResult: (Boolean) -> Unit): Boolean {
        val stroke = GestureDescription.StrokeDescription(
            path,
            0L,
            durationMs.coerceIn(MIN_GESTURE_MS, MAX_GESTURE_MS),
        )
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        return dispatchGesture(
            gesture,
            object : GestureResultCallback() {
                override fun onCompleted(description: GestureDescription?) = onResult(true)

                override fun onCancelled(description: GestureDescription?) = onResult(false)
            },
            mainHandler,
        )
    }

    companion object {
        private const val MIN_GESTURE_MS = 40L
        private const val MAX_GESTURE_MS = 3_000L

        @Volatile
        private var instance: TouchAccessibilityService? = null

        /** Le service est-il activé par l'utilisateur ? */
        val isConnected: Boolean
            get() = instance != null

        /** Capture d'écran statique : renvoie `null` si le service n'est pas actif. */
        fun capture(onResult: (Bitmap?) -> Unit) {
            val service = instance
            if (service == null) onResult(null) else service.captureScreen(onResult)
        }

        /** Glissement statique ; `false` si le service est inactif ou si le geste est refusé. */
        fun swipe(from: PointF, to: PointF, durationMs: Long, onResult: (Boolean) -> Unit): Boolean =
            instance?.performSwipe(from, to, durationMs, onResult) ?: false

        /** Appui statique ; mêmes conventions que [swipe]. */
        fun tap(at: PointF, durationMs: Long, onResult: (Boolean) -> Unit): Boolean =
            instance?.performTap(at, durationMs, onResult) ?: false

        fun screenSize(): PointF? = instance?.screenSize()
    }
}
