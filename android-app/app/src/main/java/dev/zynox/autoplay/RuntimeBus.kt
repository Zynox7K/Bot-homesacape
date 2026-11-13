package dev.zynox.autoplay

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/** Étapes d'un cycle du pilote, affichées par la bulle, la notification et l'écran. */
enum class BotState {
    IDLE,
    CAPTURING,
    THINKING,
    PLAYING,
    WAITING,
    PAUSED,
    DRY_RUN,
    ERROR,
    STOPPED,
}

/** Niveau d'un message du journal, utilisé pour sa couleur d'affichage. */
enum class LogLevel { INFO, SUCCESS, WARNING, ERROR }

/** Une ligne du journal affiché dans l'interface de contrôle. */
data class LogEntry(val timeMs: Long, val message: String, val level: LogLevel)

/**
 * État partagé du pilote.
 *
 * Le service de premier plan écrit, l'interface Compose lit : c'est l'unique
 * canal entre les deux, ce qui évite toute liaison directe entre l'`Activity` et
 * le `Service`.
 */
data class RuntimeSnapshot(
    val playing: Boolean = false,
    val state: BotState = BotState.IDLE,
    val movesPlayed: Int = 0,
    val failures: Int = 0,
    val lastReason: String = "",
    val lastScore: Float = 0f,
    val lastRoundTripMs: Long = 0L,
    val gridDescription: String = "",
    val confidence: Float = 0f,
    val engineVersion: String = "",
    val solverMode: String = "",
    val foregroundPackage: String = "",
    val message: String = "",
)

/** Bus d'état du pilote : un instantané observable et un journal borné. */
object RuntimeBus {

    private const val MAX_LOG_ENTRIES = 80

    private val snapshotFlow = MutableStateFlow(RuntimeSnapshot())
    val snapshot: StateFlow<RuntimeSnapshot> = snapshotFlow.asStateFlow()

    private val logFlow = MutableStateFlow<List<LogEntry>>(emptyList())
    val entries: StateFlow<List<LogEntry>> = logFlow.asStateFlow()

    fun update(transform: (RuntimeSnapshot) -> RuntimeSnapshot) {
        snapshotFlow.update(transform)
    }

    fun log(message: String, level: LogLevel = LogLevel.INFO) {
        if (message.isBlank()) return
        logFlow.update { (listOf(LogEntry(System.currentTimeMillis(), message, level)) + it).take(MAX_LOG_ENTRIES) }
    }

    fun clearLog() {
        logFlow.value = emptyList()
    }

    /** Remet compteurs et journal à zéro ; l'application suivie au premier plan est conservée. */
    fun resetSession() {
        val foreground = snapshotFlow.value.foregroundPackage
        snapshotFlow.value = RuntimeSnapshot(foregroundPackage = foreground)
        logFlow.value = emptyList()
    }
}
