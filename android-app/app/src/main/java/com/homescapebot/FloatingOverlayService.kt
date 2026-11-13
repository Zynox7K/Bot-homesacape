package com.homescapebot

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.IBinder
import android.os.SystemClock
import android.provider.Settings
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.*
import kotlin.math.abs

class FloatingOverlayService : Service() {
    companion object { var instance: FloatingOverlayService? = null; private set }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private lateinit var manager: WindowManager
    private var bubble: TextView? = null
    private var playing = false
    private var job: Job? = null
    private var calibrating = false
    private val channel = "bot_control"

    override fun onCreate() {
        super.onCreate()
        instance = this
        getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel(channel, "Contrôle du bot", NotificationManager.IMPORTANCE_LOW))
        val stop = PendingIntent.getService(this, 1, Intent(this, FloatingOverlayService::class.java).setAction("STOP"), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification = NotificationCompat.Builder(this, channel)
            .setSmallIcon(android.R.drawable.ic_media_play).setContentTitle("Bot local actif")
            .setContentText("Appui : lecture/pause. Appui long : calibrer.").setContentIntent(open)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Arrêter", stop).setOngoing(true).build()
        startForeground(1, notification)
        if (!Settings.canDrawOverlays(this)) { stopSelf(); return }
        manager = getSystemService(WINDOW_SERVICE) as WindowManager
        val size = (64 * resources.displayMetrics.density).toInt()
        val params = WindowManager.LayoutParams(size, size, WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN, PixelFormat.TRANSLUCENT).apply {
            gravity = Gravity.TOP or Gravity.START; x = 20; y = 180
        }
        val view = TextView(this).apply {
            text = "▶"; textSize = 26f; gravity = Gravity.CENTER; setTextColor(Color.WHITE)
            contentDescription = "Appuyer pour démarrer ou mettre en pause ; maintenir pour calibrer"
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(0xBB202A44.toInt()); setStroke(2, Color.WHITE) }
        }
        var downX = 0f; var downY = 0f; var startX = 0; var startY = 0
        val slop = 12 * resources.displayMetrics.density
        view.setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> { downX = event.rawX; downY = event.rawY; startX = params.x; startY = params.y; true }
                MotionEvent.ACTION_MOVE -> { params.x = startX + (event.rawX - downX).toInt(); params.y = startY + (event.rawY - downY).toInt(); manager.updateViewLayout(view, params); true }
                MotionEvent.ACTION_UP -> {
                    if (abs(event.rawX - downX) < slop && abs(event.rawY - downY) < slop) {
                        if (event.eventTime - event.downTime >= 650) calibrate() else toggle()
                    }
                    true
                }
                else -> true
            }
        }
        try { manager.addView(view, params); bubble = view } catch (_: Exception) { stopSelf() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "STOP") stopSelf()
        return START_NOT_STICKY
    }

    private fun calibrate() {
        if (playing || job != null) { toast("Mettez le bot en pause avant de calibrer."); return }
        if (calibrating) return
        val service = TouchAccessibilityService.instance
        if (service == null || !service.gameIsActive()) { toast("Ouvrez d'abord le plateau Homescapes."); return }
        calibrating = true
        scope.launch {
            var launched = false
            try {
                bubble?.visibility = View.INVISIBLE
                delay(150)
                val bitmap = service.capture()
                if (bitmap == null) { toast("Capture indisponible. Réessayez."); return@launch }
                if (!service.gameIsActive()) { bitmap.recycle(); toast("Retournez sur le jeu."); return@launch }
                CalibrationSession.screenshot?.recycle()
                CalibrationSession.screenshot = bitmap
                // Android 15 requires the overlay to be visible when starting an activity from the background.
                bubble?.visibility = View.VISIBLE
                try {
                    startActivity(Intent(this@FloatingOverlayService, CalibrationActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    bubble?.visibility = View.INVISIBLE
                    launched = true
                } catch (e: Exception) {
                    CalibrationSession.screenshot?.recycle(); CalibrationSession.screenshot = null
                    toast("Impossible d'ouvrir le calibrage : ${e.message ?: "erreur"}")
                }
            } finally {
                calibrating = false
                if (!launched) bubble?.visibility = View.VISIBLE
            }
        }
    }

    fun showBubble() { bubble?.visibility = View.VISIBLE }

    private fun toggle() {
        if (playing) { pause(); return }
        if (job != null || calibrating) { toast("Attendez la fin de l'opération en cours."); return }
        val service = TouchAccessibilityService.instance
        if (service == null || !service.gameIsActive()) { toast("Ouvrez d'abord le plateau Homescapes."); return }
        val config = Calibration.load(this)
        if (config == null) { toast("Maintenez la bulle pour calibrer le plateau."); return }
        playing = true; bubble?.text = "Ⅱ"
        job = scope.launch(start = CoroutineStart.LAZY) {
            try {
                while (isActive && playing) {
                    if (TouchAccessibilityService.instance !== service || !service.gameIsActive()) { toast("Ouvrez Homescapes avant de lancer le bot."); break }
                    bubble?.visibility = View.INVISIBLE
                    delay(150)
                    val captureStarted = SystemClock.elapsedRealtime()
                    val bitmap = try { service.capture() } finally { bubble?.visibility = View.VISIBLE }
                    if (bitmap == null) { toast("Capture indisponible. Réessayez."); break }
                    try {
                        if (!service.gameIsActive()) break
                        val width = bitmap.width; val height = bitmap.height
                        val move = withContext(Dispatchers.Default) { LocalEngine.analyze(bitmap, config) }
                        ensureActive()
                        if (SystemClock.elapsedRealtime() - captureStarted > 5000) { toast("Analyse trop lente, bot arrêté."); break }
                        if (move != null && playing && service.gameIsActive()) {
                            if (!service.swipe(move, width, height)) { toast("Geste refusé ou écran modifié."); break }
                        }
                    } finally { bitmap.recycle() }
                    delay(2500)
                }
            } catch (_: CancellationException) {
                // Pause is intentional; no next gesture is dispatched.
            } catch (e: Exception) {
                toast("Bot arrêté : ${e.message ?: "erreur"}")
            } finally {
                bubble?.visibility = View.VISIBLE
                playing = false; bubble?.text = "▶"; job = null
            }
        }
        job?.start()
    }
    private fun pause() { playing = false; job?.cancel(); bubble?.text = "▶" }
    private fun toast(text: String) { Toast.makeText(this, text, Toast.LENGTH_LONG).show() }
    override fun onDestroy() {
        playing = false; scope.cancel()
        bubble?.let { try { manager.removeView(it) } catch (_: Exception) {} }
        bubble = null
        if (instance === this) instance = null
        super.onDestroy()
    }
    override fun onBind(intent: Intent?): IBinder? = null
}
