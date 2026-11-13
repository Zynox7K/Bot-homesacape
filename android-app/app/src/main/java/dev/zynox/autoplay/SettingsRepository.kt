package dev.zynox.autoplay

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONException
import org.json.JSONObject

/**
 * Préférences persistantes de l'application.
 *
 * Un seul fichier (`autoplay_settings.xml`, sauvegardé par les règles de backup
 * du manifeste) porte l'adresse du moteur, la référence du dépôt GitHub et les
 * réglages du pilote. Toutes les valeurs ont un défaut utilisable : l'application
 * démarre sans aucune initialisation préalable.
 */
class SettingsRepository(context: Context) {

    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(FILE_NAME, Context.MODE_PRIVATE)

    /** Adresse du moteur distant, normalisée (schéma présent, aucune barre finale). */
    var engineUrl: String
        get() = prefs.getString(KEY_ENGINE_URL, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_ENGINE_URL, normalizeBaseUrl(value)).apply()

    /** Référence du dépôt GitHub qui versionne la configuration du moteur. */
    var repoUrl: String
        get() = prefs.getString(KEY_REPO_URL, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_REPO_URL, value.trim()).apply()

    /** Jeton d'accès personnel, requis uniquement pour un dépôt privé. */
    var githubToken: String
        get() = prefs.getString(KEY_GITHUB_TOKEN, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_GITHUB_TOKEN, value.trim()).apply()

    /** Clé partagée `AUTOPLAY_ADMIN_KEY` permettant d'écrire la configuration à chaud. */
    var adminKey: String
        get() = prefs.getString(KEY_ADMIN_KEY, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_ADMIN_KEY, value.trim()).apply()

    /** Dernière configuration récupérée du dépôt, renvoyée à chaque analyse. */
    var repoConfig: String
        get() = prefs.getString(KEY_REPO_CONFIG, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_REPO_CONFIG, value).apply()

    /** Récupère la configuration du dépôt à chaque démarrage du pilote. */
    var autoSync: Boolean
        get() = prefs.getBoolean(KEY_AUTO_SYNC, true)
        set(value) = prefs.edit().putBoolean(KEY_AUTO_SYNC, value).apply()

    /** Profondeur de calcul demandée au moteur : `fast`, `balanced` ou `expert`. */
    var solverMode: String
        get() = prefs.getString(KEY_SOLVER_MODE, DEFAULT_SOLVER_MODE).orEmpty()
        set(value) = prefs.edit().putString(KEY_SOLVER_MODE, value).apply()

    /** Intervalle minimal entre deux captures. Android en impose un d'une seconde. */
    var captureIntervalMs: Int
        get() = prefs.getInt(KEY_CAPTURE_INTERVAL_MS, DEFAULT_CAPTURE_INTERVAL_MS)
            .coerceIn(MIN_CAPTURE_INTERVAL_MS, MAX_CAPTURE_INTERVAL_MS)
        set(value) = prefs.edit()
            .putInt(KEY_CAPTURE_INTERVAL_MS, value.coerceIn(MIN_CAPTURE_INTERVAL_MS, MAX_CAPTURE_INTERVAL_MS))
            .apply()

    /** Qualité JPEG de la capture envoyée : plus elle baisse, plus l'envoi est rapide. */
    var jpegQuality: Int
        get() = prefs.getInt(KEY_JPEG_QUALITY, DEFAULT_JPEG_QUALITY).coerceIn(40, 98)
        set(value) = prefs.edit().putInt(KEY_JPEG_QUALITY, value.coerceIn(40, 98)).apply()

    /** Largeur, en pixels, de l'image transmise au moteur. */
    var analysisWidth: Int
        get() = prefs.getInt(KEY_ANALYSIS_WIDTH, DEFAULT_ANALYSIS_WIDTH)
            .coerceIn(MIN_ANALYSIS_WIDTH, MAX_ANALYSIS_WIDTH)
        set(value) = prefs.edit()
            .putInt(KEY_ANALYSIS_WIDTH, value.coerceIn(MIN_ANALYSIS_WIDTH, MAX_ANALYSIS_WIDTH))
            .apply()

    /** Multiplicateur de la durée des gestes : 1,0 suit la demande du moteur. */
    var gestureScale: Float
        get() = prefs.getFloat(KEY_GESTURE_SCALE, 1.0f).coerceIn(0.5f, 2.5f)
        set(value) = prefs.edit().putFloat(KEY_GESTURE_SCALE, value.coerceIn(0.5f, 2.5f)).apply()

    /** Délai maximal accordé au moteur pour répondre à une analyse. */
    var requestTimeoutMs: Int
        get() = prefs.getInt(KEY_REQUEST_TIMEOUT_MS, DEFAULT_REQUEST_TIMEOUT_MS)
            .coerceIn(2_000, 30_000)
        set(value) = prefs.edit()
            .putInt(KEY_REQUEST_TIMEOUT_MS, value.coerceIn(2_000, 30_000))
            .apply()

    /** Couleur prioritaire (indice dans la palette du moteur), -1 = automatique. */
    var goalColor: Int
        get() = prefs.getInt(KEY_GOAL_COLOR, -1)
        set(value) = prefs.edit().putInt(KEY_GOAL_COLOR, value).apply()

    /** Paquet du jeu : le pilote se met en pause s'il n'est pas au premier plan. */
    var targetPackage: String
        get() = prefs.getString(KEY_TARGET_PACKAGE, DEFAULT_TARGET_PACKAGE).orEmpty()
        set(value) = prefs.edit().putString(KEY_TARGET_PACKAGE, value.trim()).apply()

    var pauseOffGame: Boolean
        get() = prefs.getBoolean(KEY_PAUSE_OFF_GAME, true)
        set(value) = prefs.edit().putBoolean(KEY_PAUSE_OFF_GAME, value).apply()

    /** Analyse sans exécuter de geste : utile pour vérifier la détection. */
    var dryRun: Boolean
        get() = prefs.getBoolean(KEY_DRY_RUN, false)
        set(value) = prefs.edit().putBoolean(KEY_DRY_RUN, value).apply()

    var bubbleX: Int
        get() = prefs.getInt(KEY_BUBBLE_X, -1)
        set(value) = prefs.edit().putInt(KEY_BUBBLE_X, value).apply()

    var bubbleY: Int
        get() = prefs.getInt(KEY_BUBBLE_Y, -1)
        set(value) = prefs.edit().putInt(KEY_BUBBLE_Y, value).apply()

    /** Configuration du dépôt sous forme d'objet JSON, ou `null` si absente/invalide. */
    fun repoConfigOrNull(): JSONObject? {
        val raw = repoConfig
        if (raw.isBlank()) return null
        return try {
            JSONObject(raw)
        } catch (error: JSONException) {
            null
        }
    }

    /** Position de la bulle, repliée dans l'écran et par défaut à droite. */
    fun bubblePosition(screenWidth: Int, screenHeight: Int, bubbleSize: Int): Pair<Int, Int> {
        val maxX = (screenWidth - bubbleSize).coerceAtLeast(0)
        val maxY = (screenHeight - bubbleSize).coerceAtLeast(0)
        val x = bubbleX.takeIf { it >= 0 } ?: (maxX - DEFAULT_BUBBLE_MARGIN_PX)
        val y = bubbleY.takeIf { it >= 0 } ?: (screenHeight * 2 / 5)
        return x.coerceIn(0, maxX) to y.coerceIn(0, maxY)
    }

    companion object {
        const val FILE_NAME = "autoplay_settings"

        const val MODE_FAST = "fast"
        const val MODE_BALANCED = "balanced"
        const val MODE_EXPERT = "expert"
        val SOLVER_MODES = listOf(MODE_FAST, MODE_BALANCED, MODE_EXPERT)

        const val MIN_CAPTURE_INTERVAL_MS = 1_000
        const val MAX_CAPTURE_INTERVAL_MS = 5_000
        const val MIN_ANALYSIS_WIDTH = 320
        const val MAX_ANALYSIS_WIDTH = 900

        private const val DEFAULT_SOLVER_MODE = MODE_EXPERT
        private const val DEFAULT_CAPTURE_INTERVAL_MS = 1_200
        private const val DEFAULT_JPEG_QUALITY = 78
        private const val DEFAULT_ANALYSIS_WIDTH = 540
        private const val DEFAULT_REQUEST_TIMEOUT_MS = 8_000
        private const val DEFAULT_TARGET_PACKAGE = "com.playrix.homescapes"
        private const val DEFAULT_BUBBLE_MARGIN_PX = 24

        private const val KEY_ENGINE_URL = "engine_url"
        private const val KEY_REPO_URL = "repo_url"
        private const val KEY_GITHUB_TOKEN = "github_token"
        private const val KEY_ADMIN_KEY = "admin_key"
        private const val KEY_REPO_CONFIG = "repo_config"
        private const val KEY_AUTO_SYNC = "auto_sync"
        private const val KEY_SOLVER_MODE = "solver_mode"
        private const val KEY_CAPTURE_INTERVAL_MS = "capture_interval_ms"
        private const val KEY_JPEG_QUALITY = "jpeg_quality"
        private const val KEY_ANALYSIS_WIDTH = "analysis_width"
        private const val KEY_GESTURE_SCALE = "gesture_scale"
        private const val KEY_REQUEST_TIMEOUT_MS = "request_timeout_ms"
        private const val KEY_GOAL_COLOR = "goal_color"
        private const val KEY_TARGET_PACKAGE = "target_package"
        private const val KEY_PAUSE_OFF_GAME = "pause_off_game"
        private const val KEY_DRY_RUN = "dry_run"
        private const val KEY_BUBBLE_X = "bubble_x"
        private const val KEY_BUBBLE_Y = "bubble_y"

        /**
         * Complète le schéma et retire les barres de fin : l'utilisateur peut
         * saisir `moteur.exemple.com` comme `https://moteur.exemple.com/`.
         */
        fun normalizeBaseUrl(raw: String): String {
            val text = raw.trim().trimEnd('/')
            if (text.isEmpty()) return ""
            return if (text.startsWith("http://") || text.startsWith("https://")) text else "https://$text"
        }
    }
}
