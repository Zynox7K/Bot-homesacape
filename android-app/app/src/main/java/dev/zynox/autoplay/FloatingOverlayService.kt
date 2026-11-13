package dev.zynox.autoplay

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.PointF
import android.graphics.drawable.Icon
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.WindowManager
import dev.zynox.autoplay.overlay.BubbleView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.ByteArrayOutputStream
import java.util.UUID
import kotlin.coroutines.resume

/**
 * Service de premier plan qui tient la bulle flottante et la boucle de pilotage.
 *
 * Un cycle complet : capture d'écran via le service d'accessibilité, réduction et
 * encodage JPEG, envoi au moteur distant, réception du geste en coordonnées
 * normalisées, exécution par `dispatchGesture`, attente de la stabilisation des
 * animations. Le téléphone ne reconnaît rien et ne calcule rien : il photographie
 * et il joue.
 *
 * La boucle tourne sur [Dispatchers.Default], l'encodage sur [Dispatchers.Default]
 * également, l'appel réseau sur [Dispatchers.IO] et toute mise à jour de la bulle
 * est repostée sur le fil principal. Rien ne bloque jamais l'interface du jeu.
 */
class FloatingOverlayService : Service(), BubbleView.Callbacks {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val mainHandler = Handler(Looper.getMainLooper())

    private lateinit var settings: SettingsRepository
    private lateinit var windowManager: WindowManager

    private var loopJob: Job? = null
    private var bubble: BubbleView? = null
    private var bubbleParams: WindowManager.LayoutParams? = null
    private var wakeLock: PowerManager.WakeLock? = null

    @Volatile private var playing = false
    @Volatile private var settleMs = DEFAULT_SETTLE_MS
    @Volatile private var serverIntervalMs = SettingsRepository.MIN_CAPTURE_INTERVAL_MS.toLong()

    private var sessionId = UUID.randomUUID().toString()
    private var offGameLogged = false
    private var staleLogged = false
    private var lastGridDescription = ""

    // ------------------------------------------------------------------ //
    // Cycle de vie
    // ------------------------------------------------------------------ #

    override fun onCreate() {
        super.onCreate()
        settings = SettingsRepository(this)
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        isRunning = true
        createNotificationChannel()
        startForeground(
            NOTIFICATION_ID,
            buildNotification(),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
        )
        RuntimeBus.resetSession()
        RuntimeBus.log(getString(R.string.log_service_started), LogLevel.SUCCESS)
        if (!attachBubble()) {
            stopSelfAndCleanup()
            return
        }
        loopJob = scope.launch { controlLoop() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_TOGGLE -> togglePlayback()
            ACTION_STOP -> {
                stopSelfAndCleanup()
                return START_NOT_STICKY
            }
            // ACTION_START : le service vient d'être créé, la bulle reste en veille
            // et l'utilisateur la lance d'un appui.
            else -> Unit
        }
        updateNotification()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        playing = false
        loopJob?.cancel()
        scope.cancel()
        releaseWakeLock()
        detachBubble()
        isRunning = false
        RuntimeBus.update { it.copy(playing = false, state = BotState.STOPPED, message = "") }
        RuntimeBus.log(getString(R.string.log_service_stopped))
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ------------------------------------------------------------------ //
    // Bulle flottante
    // ------------------------------------------------------------------ #

    private fun attachBubble(): Boolean {
        if (!Settings.canDrawOverlays(this)) {
            RuntimeBus.log(getString(R.string.log_overlay_denied), LogLevel.ERROR)
            return false
        }
        val view = BubbleView(this, this)
        val screen = TouchAccessibilityService.screenSize() ?: fallbackScreenSize()
        val (x, y) = settings.bubblePosition(
            screenWidth = screen.x.toInt(),
            screenHeight = screen.y.toInt(),
            bubbleSize = BubbleView.sizePx(this),
        )
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            this.x = x
            this.y = y
        }
        return try {
            windowManager.addView(view, params)
            bubble = view
            bubbleParams = params
            true
        } catch (error: RuntimeException) {
            Log.e(TAG, "superposition impossible", error)
            RuntimeBus.log(getString(R.string.log_overlay_failed, describeError(error)), LogLevel.ERROR)
            false
        }
    }

    private fun detachBubble() {
        val view = bubble ?: return
        bubble = null
        bubbleParams = null
        try {
            windowManager.removeViewImmediate(view)
        } catch (error: IllegalArgumentException) {
            // Vue déjà détachée : rien à faire.
        }
    }

    private fun fallbackScreenSize(): PointF {
        val metrics = resources.displayMetrics
        return PointF(metrics.widthPixels.toFloat(), metrics.heightPixels.toFloat())
    }

    override fun onDrag(deltaX: Int, deltaY: Int) {
        val view = bubble ?: return
        val params = bubbleParams ?: return
        val screen = fallbackScreenSize()
        val maxX = (screen.x.toInt() - view.width).coerceAtLeast(0)
        val maxY = (screen.y.toInt() - view.height).coerceAtLeast(0)
        params.x = (params.x + deltaX).coerceIn(0, maxX)
        params.y = (params.y + deltaY).coerceIn(0, maxY)
        try {
            windowManager.updateViewLayout(view, params)
        } catch (error: IllegalArgumentException) {
            // Vue détachée pendant le glissement : le déplacement s'arrête là.
        }
    }

    override fun onDragEnd() {
        val params = bubbleParams ?: return
        settings.bubbleX = params.x
        settings.bubbleY = params.y
    }

    override fun onTap() = togglePlayback()

    override fun onLongPress() {
        startActivity(
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        )
    }

    private fun syncBubble() {
        val state = RuntimeBus.snapshot.value.state
        val active = playing
        mainHandler.post {
            val view = bubble ?: return@post
            view.playing = active
            view.state = state
        }
    }

    // ------------------------------------------------------------------ //
    // Commandes
    // ------------------------------------------------------------------ #

    private fun togglePlayback() {
        if (!playing) {
            if (!TouchAccessibilityService.isConnected) {
                fail(getString(R.string.error_accessibility_missing))
                return
            }
            if (settings.engineUrl.isBlank()) {
                fail(getString(R.string.error_engine_missing))
                return
            }
            playing = true
            sessionId = UUID.randomUUID().toString()
            settleMs = DEFAULT_SETTLE_MS
            serverIntervalMs = SettingsRepository.MIN_CAPTURE_INTERVAL_MS.toLong()
            lastGridDescription = ""
            offGameLogged = false
            staleLogged = false
            RuntimeBus.update {
                it.copy(playing = true, state = BotState.IDLE, movesPlayed = 0, failures = 0, message = "")
            }
            RuntimeBus.log(getString(R.string.log_started_playing), LogLevel.SUCCESS)
            if (settings.autoSync) launchSync()
            acquireWakeLock()
        } else {
            playing = false
            RuntimeBus.update { it.copy(playing = false, state = BotState.PAUSED, message = "") }
            RuntimeBus.log(getString(R.string.log_paused))
            releaseWakeLock()
        }
        syncBubble()
        updateNotification()
    }

    private fun launchSync() {
        scope.launch {
            val outcome = ConfigSync.synchronize(this@FloatingOverlayService, settings)
            RuntimeBus.log(
                outcome.message,
                if (outcome.ok) LogLevel.SUCCESS else LogLevel.WARNING,
            )
        }
    }

    /** Le pilote ne peut pas démarrer : on le laisse en erreur, bulle et notification à jour. */
    private fun fail(message: String) {
        playing = false
        releaseWakeLock()
        RuntimeBus.update { it.copy(playing = false) }
        reportFailure(message)
    }

    /** Journalise un échec, met à jour l'état affiché et rafraîchit bulle et notification. */
    private fun reportFailure(
        message: String,
        level: LogLevel = LogLevel.ERROR,
        state: BotState = BotState.ERROR,
    ) {
        Log.w(TAG, message)
        RuntimeBus.log(message, level)
        RuntimeBus.update { it.copy(state = state, message = message) }
        syncBubble()
        updateNotification()
    }

    private fun stopSelfAndCleanup() {
        playing = false
        loopJob?.cancel()
        releaseWakeLock()
        detachBubble()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    // ------------------------------------------------------------------ //
    // Boucle de pilotage
    // ------------------------------------------------------------------ #

    private suspend fun controlLoop() {
        while (scope.isActive) {
            if (!playing) {
                delay(IDLE_POLL_MS)
                continue
            }
            if (isGameInBackground()) {
                if (!offGameLogged) {
                    offGameLogged = true
                    RuntimeBus.log(getString(R.string.log_paused_off_game), LogLevel.WARNING)
                }
                RuntimeBus.update {
                    it.copy(state = BotState.PAUSED, message = getString(R.string.log_paused_off_game))
                }
                syncBubble()
                updateNotification()
                delay(IDLE_POLL_MS)
                continue
            }
            if (offGameLogged) {
                offGameLogged = false
                RuntimeBus.log(getString(R.string.log_resumed_in_game), LogLevel.SUCCESS)
            }

            val startedAt = SystemClock.elapsedRealtime()
            val succeeded = playRound()
            RuntimeBus.update { it.copy(failures = if (succeeded) 0 else it.failures + 1) }

            if (RuntimeBus.snapshot.value.failures >= MAX_CONSECUTIVE_FAILURES) {
                val message = getString(
                    R.string.error_stopped_after_failures,
                    RuntimeBus.snapshot.value.failures,
                )
                playing = false
                releaseWakeLock()
                RuntimeBus.update { it.copy(playing = false) }
                reportFailure(message)
                delay(IDLE_POLL_MS)
                continue
            }

            val elapsed = SystemClock.elapsedRealtime() - startedAt
            val pause = maxOf(
                settings.captureIntervalMs.toLong(),
                settleMs,
                serverIntervalMs,
            ).coerceAtLeast(MIN_STEP_MS) - elapsed
            RuntimeBus.update { it.copy(state = BotState.WAITING) }
            syncBubble()
            delay(pause.coerceAtLeast(0L))
        }
    }

    /** Une capture complète : écran -> JPEG -> moteur -> geste. Retourne `true` en cas de succès. */
    private suspend fun playRound(): Boolean {
        val screen = TouchAccessibilityService.screenSize() ?: fallbackScreenSize()
        val screenWidth = screen.x.toInt()
        val screenHeight = screen.y.toInt()

        RuntimeBus.update { it.copy(state = BotState.CAPTURING, message = "") }
        syncBubble()

        val captured = withTimeoutOrNull(CAPTURE_TIMEOUT_MS) { captureScreen() }
        if (captured == null) {
            reportFailure(getString(R.string.error_capture_failed))
            return false
        }

        val jpeg = withContext(Dispatchers.Default) { encodeJpeg(captured) }
        captured.recycle()
        if (jpeg == null) {
            reportFailure(getString(R.string.error_capture_failed))
            return false
        }

        RuntimeBus.update {
            it.copy(state = if (settings.dryRun) BotState.DRY_RUN else BotState.THINKING)
        }
        syncBubble()

        val sentAt = SystemClock.elapsedRealtime()
        val analysis = try {
            withContext(Dispatchers.IO) {
                ApiClient.analyze(
                    baseUrl = settings.engineUrl,
                    jpeg = jpeg,
                    options = ApiClient.AnalyzeOptions(
                        sessionId = sessionId,
                        displayWidth = screenWidth,
                        displayHeight = screenHeight,
                        solverMode = settings.solverMode,
                        goalColor = settings.goalColor,
                        timeoutMs = settings.requestTimeoutMs,
                        configOverride = settings.repoConfigOrNull(),
                    ),
                )
            }
        } catch (error: Exception) {
            reportFailure(getString(R.string.error_network, describeError(error)))
            return false
        }

        val roundTrip = SystemClock.elapsedRealtime() - sentAt
        settleMs = analysis.settleMs.toLong()
        serverIntervalMs = analysis.captureIntervalMs.toLong()
        reportAnalysis(analysis, roundTrip)

        if (!analysis.ok) {
            val message = if (analysis.message.isNotBlank()) {
                getString(R.string.error_engine_failed, analysis.message)
            } else {
                getString(R.string.error_no_board)
            }
            reportFailure(message)
            return false
        }

        val move = analysis.move
        if (move == null) {
            reportFailure(getString(R.string.error_no_move), LogLevel.WARNING, BotState.WAITING)
            return true
        }

        if (settings.dryRun) {
            RuntimeBus.log(getString(R.string.log_dry_run_move, move.reason), LogLevel.INFO)
            updateNotification()
            return true
        }

        val from = move.screenFrom(screenWidth, screenHeight)
        val to = move.screenTo(screenWidth, screenHeight)
        val duration = (move.durationMs * settings.gestureScale).toLong()
            .coerceIn(MIN_GESTURE_MS, MAX_GESTURE_MS)

        RuntimeBus.update { it.copy(state = BotState.PLAYING, message = "") }
        syncBubble()

        // Le geste est borné : si le système ne rappelle jamais sa fin, le pilote
        // repart au tour suivant au lieu de se figer.
        val performed = withTimeoutOrNull(GESTURE_TIMEOUT_MS) {
            if (to == null) performTap(from, duration) else performSwipe(from, to, duration)
        } ?: false
        if (!performed) {
            reportFailure(getString(R.string.error_gesture_failed))
            return false
        }

        RuntimeBus.update { it.copy(movesPlayed = it.movesPlayed + 1, state = BotState.WAITING) }
        RuntimeBus.log(getString(R.string.log_move_played, move.reason), LogLevel.SUCCESS)
        syncBubble()
        updateNotification()
        return true
    }

    /** Publie le détail de l'analyse ; le résumé n'est journalisé qu'à chaque changement de grille. */
    private fun reportAnalysis(analysis: ApiClient.Analysis, roundTripMs: Long) {
        val grid = if (analysis.rows > 0 && analysis.cols > 0) {
            getString(R.string.format_grid, analysis.rows, analysis.cols)
        } else {
            ""
        }
        RuntimeBus.update {
            it.copy(
                lastReason = analysis.move?.reason ?: analysis.message,
                lastScore = analysis.move?.score ?: 0f,
                lastRoundTripMs = roundTripMs,
                gridDescription = grid,
                confidence = analysis.confidence,
                engineVersion = analysis.engineVersion,
                solverMode = analysis.solverMode,
            )
        }
        if (grid.isNotEmpty() && grid != lastGridDescription) {
            lastGridDescription = grid
            RuntimeBus.log(
                getString(
                    R.string.log_analysis_summary,
                    analysis.rows,
                    analysis.cols,
                    analysis.pieces,
                    analysis.blocks + analysis.holes,
                    analysis.specials,
                    roundTripMs.toInt(),
                ),
                LogLevel.INFO,
            )
        }
        if (analysis.stale && !staleLogged) {
            staleLogged = true
            RuntimeBus.log(getString(R.string.log_stale_session), LogLevel.WARNING)
        } else if (!analysis.stale) {
            staleLogged = false
        }
    }

    private fun isGameInBackground(): Boolean {
        if (!settings.pauseOffGame) return false
        val target = settings.targetPackage
        if (target.isBlank()) return false
        val foreground = RuntimeBus.snapshot.value.foregroundPackage
        // Tant qu'aucune fenêtre n'a été observée, on ne bloque pas le pilote.
        if (foreground.isBlank()) return false
        return foreground != target
    }

    // ------------------------------------------------------------------ //
    // Ponts suspendus vers le service d'accessibilité
    // ------------------------------------------------------------------ #

    private suspend fun captureScreen(): Bitmap? = suspendCancellableCoroutine { continuation ->
        TouchAccessibilityService.capture { bitmap ->
            if (continuation.isActive) continuation.resume(bitmap)
        }
    }

    private suspend fun performSwipe(from: PointF, to: PointF, durationMs: Long): Boolean =
        suspendCancellableCoroutine { continuation ->
            val dispatched = TouchAccessibilityService.swipe(from, to, durationMs) { completed ->
                if (continuation.isActive) continuation.resume(completed)
            }
            if (!dispatched && continuation.isActive) continuation.resume(false)
        }

    private suspend fun performTap(at: PointF, durationMs: Long): Boolean =
        suspendCancellableCoroutine { continuation ->
            val dispatched = TouchAccessibilityService.tap(at, durationMs) { completed ->
                if (continuation.isActive) continuation.resume(completed)
            }
            if (!dispatched && continuation.isActive) continuation.resume(false)
        }

    /**
     * Réduit la capture à la largeur d'analyse puis l'encode en JPEG.
     *
     * Une capture 1080 × 2400 passe à environ 120 Ko : c'est ce qui rend l'aller-
     * retour compatible avec une partie en temps réel, y compris en 4G.
     */
    private fun encodeJpeg(source: Bitmap): ByteArray? {
        val targetWidth = settings.analysisWidth
        val scaled = if (source.width > targetWidth) {
            val ratio = targetWidth.toFloat() / source.width.toFloat()
            val targetHeight = (source.height * ratio).toInt().coerceAtLeast(1)
            Bitmap.createScaledBitmap(source, targetWidth, targetHeight, true)
        } else {
            source
        }
        val output = ByteArrayOutputStream(scaled.width * scaled.height / 4)
        val encoded = scaled.compress(Bitmap.CompressFormat.JPEG, settings.jpegQuality, output)
        if (scaled !== source) scaled.recycle()
        return if (encoded && output.size() > 0) output.toByteArray() else null
    }

    // ------------------------------------------------------------------ //
    // Notification et verrou d'activité
    // ------------------------------------------------------------------ #

    private fun createNotificationChannel() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notif_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.notif_channel_description)
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val snapshot = RuntimeBus.snapshot.value
        val openIntent = PendingIntent.getActivity(
            this,
            REQUEST_OPEN,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val toggleIntent = PendingIntent.getService(
            this,
            REQUEST_TOGGLE,
            Intent(this, FloatingOverlayService::class.java).setAction(ACTION_TOGGLE),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stopIntent = PendingIntent.getService(
            this,
            REQUEST_STOP,
            Intent(this, FloatingOverlayService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val title = when {
            snapshot.state == BotState.ERROR -> getString(R.string.notif_error)
            playing -> getString(R.string.notif_playing)
            snapshot.state == BotState.PAUSED -> getString(R.string.notif_paused)
            else -> getString(R.string.notif_idle)
        }
        val detail = snapshot.message.ifBlank {
            getString(R.string.notif_detail, stateLabel(snapshot.state), snapshot.movesPlayed)
        }
        val toggleIcon = if (playing) R.drawable.ic_notification_pause else R.drawable.ic_notification_play

        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(if (playing) R.drawable.ic_notification_play else R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(detail)
            .setStyle(Notification.BigTextStyle().bigText(detail))
            .setContentIntent(openIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .addAction(
                Notification.Action.Builder(
                    Icon.createWithResource(this, toggleIcon),
                    getString(R.string.notif_action_toggle),
                    toggleIntent,
                ).build(),
            )
            .addAction(
                Notification.Action.Builder(
                    Icon.createWithResource(this, R.drawable.ic_notification),
                    getString(R.string.notif_action_stop),
                    stopIntent,
                ).build(),
            )
            .build()
    }

    private fun updateNotification() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        manager.notify(NOTIFICATION_ID, buildNotification())
    }

    private fun stateLabel(state: BotState): String = getString(
        when (state) {
            BotState.IDLE -> R.string.state_idle
            BotState.CAPTURING -> R.string.state_capturing
            BotState.THINKING -> R.string.state_thinking
            BotState.PLAYING -> R.string.state_playing
            BotState.WAITING -> R.string.state_waiting
            BotState.PAUSED -> R.string.state_paused
            BotState.DRY_RUN -> R.string.state_dry_run
            BotState.ERROR -> R.string.state_error
            BotState.STOPPED -> R.string.state_stopped
        },
    )

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        val manager = getSystemService(Context.POWER_SERVICE) as? PowerManager ?: return
        val lock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, WAKE_LOCK_TAG)
        lock.setReferenceCounted(false)
        lock.acquire(WAKE_LOCK_TIMEOUT_MS)
        wakeLock = lock
    }

    private fun releaseWakeLock() {
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
    }

    companion object {
        const val ACTION_TOGGLE = "dev.zynox.autoplay.action.TOGGLE"
        const val ACTION_START = "dev.zynox.autoplay.action.START"
        const val ACTION_STOP = "dev.zynox.autoplay.action.STOP"

        private const val TAG = "AutoPlay.Pilot"
        private const val CHANNEL_ID = "autoplay_pilotage"
        private const val NOTIFICATION_ID = 4211
        private const val REQUEST_OPEN = 10
        private const val REQUEST_TOGGLE = 11
        private const val REQUEST_STOP = 12

        private const val IDLE_POLL_MS = 250L
        private const val MIN_STEP_MS = 200L
        private const val CAPTURE_TIMEOUT_MS = 4_000L
        private const val GESTURE_TIMEOUT_MS = 5_000L
        private const val MIN_GESTURE_MS = 40L
        private const val MAX_GESTURE_MS = 2_000L
        private const val DEFAULT_SETTLE_MS = 1_250L
        private const val MAX_CONSECUTIVE_FAILURES = 5
        private const val WAKE_LOCK_TAG = "autoplay::pilot"
        private const val WAKE_LOCK_TIMEOUT_MS = 4 * 60 * 60 * 1000L

        /** Le service est-il vivant ? Consulté par l'écran de contrôle. */
        @Volatile
        var isRunning = false
            private set

        /** Démarre le service sans lancer le pilote : la bulle apparaît en veille. */
        fun start(context: Context) {
            context.startForegroundService(
                Intent(context, FloatingOverlayService::class.java).setAction(ACTION_START),
            )
        }

        /** Bascule lecture/pause, que le service soit déjà vivant ou non. */
        fun toggle(context: Context) {
            context.startService(
                Intent(context, FloatingOverlayService::class.java).setAction(ACTION_TOGGLE),
            )
        }

        fun stop(context: Context) {
            context.startService(
                Intent(context, FloatingOverlayService::class.java).setAction(ACTION_STOP),
            )
        }
    }
}
