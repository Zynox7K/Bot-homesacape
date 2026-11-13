package dev.zynox.autoplay

import android.graphics.PointF
import org.json.JSONException
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import java.nio.charset.StandardCharsets
import java.util.UUID
import javax.net.ssl.SSLException

/**
 * Client HTTP du moteur distant.
 *
 * Aucun client réseau tiers n'est embarqué : [HttpURLConnection] et `org.json`
 * font partie du framework Android, ce qui garde l'APK léger et le comportement
 * prévisible. Les deux points d'entrée utiles au pilote sont [analyze] (capture
 * contre geste) et [health] (sonde de vie) ; [fetchRepoConfig] et
 * [writeEngineConfig] servent à la synchronisation depuis le dépôt GitHub.
 *
 * Toutes les méthodes sont bloquantes : elles doivent être appelées depuis un
 * `Dispatchers.IO`, jamais depuis le fil principal.
 */
object ApiClient {

    /** Erreur renvoyée par le moteur, ou erreur de transport. */
    class EngineException(message: String, val statusCode: Int = 0) : IOException(message)

    /** Coordonnées normalisées (0 à 1) relatives à l'image envoyée. */
    data class Point(val x: Float, val y: Float)

    /** Geste à exécuter côté téléphone. */
    data class Move(
        val kind: String,
        val from: Point,
        val to: Point?,
        val durationMs: Int,
        val score: Float,
        val reason: String,
        val combo: Boolean,
    ) {
        /** Projette le point de départ normalisé en pixels écran. */
        fun screenFrom(width: Int, height: Int) = PointF(from.x * width, from.y * height)

        /** Projette le point d'arrivée normalisé en pixels écran (nul pour un appui simple). */
        fun screenTo(width: Int, height: Int): PointF? = to?.let { PointF(it.x * width, it.y * height) }
    }

    /** Résultat complet d'une analyse. */
    data class Analysis(
        val ok: Boolean,
        val message: String,
        val move: Move?,
        val rows: Int,
        val cols: Int,
        val confidence: Float,
        val pieces: Int,
        val blocks: Int,
        val holes: Int,
        val specials: Int,
        val settleMs: Int,
        val captureIntervalMs: Int,
        val stale: Boolean,
        val solverMode: String,
        val engineVersion: String,
        val serverTotalMs: Int,
    )

    /** Réponse de `GET /health`. */
    data class Health(
        val version: String,
        val solverMode: String,
        val templates: Int,
        val uptimeSeconds: Double,
        val latencyMs: Long,
    )

    /** Paramètres d'un appel à `POST /analyze`. */
    data class AnalyzeOptions(
        val sessionId: String,
        val displayWidth: Int,
        val displayHeight: Int,
        val solverMode: String,
        val goalColor: Int,
        val timeoutMs: Int,
        val configOverride: JSONObject?,
    )

    private const val DEFAULT_TIMEOUT_MS = 8_000
    private const val USER_AGENT = "AutoPlay-Android/1.0"
    private const val REPO_CONFIG_PATH = "bot-engine/config.json"
    private const val GITHUB_API_VERSION = "2022-11-28"
    private val VALID_SEGMENT = Regex("^[A-Za-z0-9._-]+$")

    // ------------------------------------------------------------------ //
    // Moteur
    // ------------------------------------------------------------------ //

    /** Sonde de vie du moteur, avec mesure de latence aller-retour. */
    fun health(baseUrl: String, timeoutMs: Int = DEFAULT_TIMEOUT_MS): Health {
        val startedAt = System.currentTimeMillis()
        val body = request(endpoint(baseUrl, "/health"), "GET", timeoutMs)
        val json = parseObject(body, "/health")
        return Health(
            version = json.optString("version", ""),
            solverMode = json.optString("solver_mode", ""),
            templates = json.optJSONArray("templates")?.length() ?: 0,
            uptimeSeconds = json.optDouble("uptime_s", 0.0),
            latencyMs = System.currentTimeMillis() - startedAt,
        )
    }

    /**
     * Envoie une capture JPEG et renvoie le geste à jouer.
     *
     * L'image part en `multipart/form-data` (champ binaire `image`) : c'est le
     * transport le plus rapide, sans le surcoût de 33 % du base64.
     */
    fun analyze(baseUrl: String, jpeg: ByteArray, options: AnalyzeOptions): Analysis {
        if (jpeg.isEmpty()) throw EngineException("capture vide")

        val fields = mutableListOf(
            "session_id" to options.sessionId,
            "display_width" to options.displayWidth.toString(),
            "display_height" to options.displayHeight.toString(),
        )
        if (options.goalColor >= 0) {
            fields += "goal_color" to options.goalColor.toString()
        }
        buildConfigField(options.configOverride, options.solverMode)?.let { fields += "config" to it }

        val boundary = "autoplay-" + UUID.randomUUID().toString().replace("-", "")
        val body = buildMultipart(boundary, fields, jpeg)
        val response = request(
            url = endpoint(baseUrl, "/analyze"),
            method = "POST",
            timeoutMs = options.timeoutMs.coerceAtLeast(2_000),
            body = body,
            contentType = "multipart/form-data; boundary=$boundary",
        )
        return parseAnalysis(parseObject(response, "/analyze"))
    }

    /**
     * Applique une configuration à chaud sur le moteur (`POST /config`).
     * Nécessite la clé partagée `AUTOPLAY_ADMIN_KEY` du serveur.
     */
    fun writeEngineConfig(
        baseUrl: String,
        adminKey: String,
        config: JSONObject,
        timeoutMs: Int = DEFAULT_TIMEOUT_MS,
    ): JSONObject {
        if (adminKey.isBlank()) throw EngineException("clé d'administration absente")
        val payload = config.toString().toByteArray(StandardCharsets.UTF_8)
        val response = request(
            url = endpoint(baseUrl, "/config"),
            method = "POST",
            timeoutMs = timeoutMs,
            headers = listOf("X-Engine-Key" to adminKey.trim()),
            body = payload,
            contentType = "application/json; charset=utf-8",
        )
        return parseObject(response, "/config")
    }

    // ------------------------------------------------------------------ //
    // Dépôt GitHub
    // ------------------------------------------------------------------ //

    /**
     * Récupère `bot-engine/config.json` depuis le dépôt via l'API GitHub.
     *
     * Le jeton n'est requis que pour un dépôt privé ; il doit disposer du droit
     * de lecture sur le contenu.
     */
    fun fetchRepoConfig(
        repoUrl: String,
        token: String = "",
        timeoutMs: Int = 12_000,
    ): JSONObject {
        val (owner, repo) = parseRepo(repoUrl)
            ?: throw EngineException("URL de dépôt invalide : $repoUrl")
        val headers = mutableListOf(
            "Accept" to "application/vnd.github.raw+json",
            "X-GitHub-Api-Version" to GITHUB_API_VERSION,
        )
        if (token.isNotBlank()) headers += "Authorization" to "Bearer ${token.trim()}"
        val body = request(
            url = "https://api.github.com/repos/$owner/$repo/contents/$REPO_CONFIG_PATH",
            method = "GET",
            timeoutMs = timeoutMs,
            headers = headers,
        )
        return parseObject(body, REPO_CONFIG_PATH)
    }

    /**
     * Extrait `proprietaire` et `depot` d'une référence GitHub.
     * Formes acceptées : URL complète (avec ou sans `.git`, avec ou sans chemin
     * supplémentaire), forme courte `proprietaire/depot`, URL SCP `git@…`.
     */
    fun parseRepo(raw: String): Pair<String, String>? {
        val text = raw.trim().trimEnd('/')
        if (text.isEmpty()) return null
        val body = when {
            text.startsWith("git@github.com:") -> text.removePrefix("git@github.com:")
            text.startsWith("ssh://git@github.com/") -> text.removePrefix("ssh://git@github.com/")
            text.contains("github.com/") -> text.substringAfter("github.com/")
            else -> text
        }
        val parts = body.removeSuffix(".git").split("/", limit = 3)
        if (parts.size < 2) return null
        val owner = parts[0]
        val repo = parts[1]
        if (!VALID_SEGMENT.matches(owner) || !VALID_SEGMENT.matches(repo)) return null
        return owner to repo
    }

    // ------------------------------------------------------------------ //
    // Transport
    // ------------------------------------------------------------------ //

    private fun endpoint(baseUrl: String, path: String): String {
        val root = baseUrl.trim().trimEnd('/')
        if (root.isEmpty()) throw EngineException("adresse du moteur absente")
        return root + path
    }

    private fun request(
        url: String,
        method: String,
        timeoutMs: Int,
        headers: List<Pair<String, String>> = emptyList(),
        body: ByteArray? = null,
        contentType: String? = null,
    ): String {
        val connection = URL(url).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            connection.instanceFollowRedirects = true
            connection.useCaches = false
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("User-Agent", USER_AGENT)
            for ((name, value) in headers) connection.setRequestProperty(name, value)

            if (body != null) {
                connection.doOutput = true
                if (contentType != null) connection.setRequestProperty("Content-Type", contentType)
                connection.setFixedLengthStreamingMode(body.size)
                connection.outputStream.use { it.write(body) }
            }

            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.use { it.readBytes() }?.toString(StandardCharsets.UTF_8).orEmpty()
            if (status !in 200..299) {
                throw EngineException(describeError(text, status, connection.responseMessage), status)
            }
            return text
        } catch (error: EngineException) {
            throw error
        } catch (error: SSLException) {
            throw EngineException("connexion chiffrée refusée (${error.message ?: "certificat"})")
        } catch (error: SocketTimeoutException) {
            throw EngineException("délai dépassé après $timeoutMs ms")
        } catch (error: UnknownHostException) {
            throw EngineException("hôte inconnu : ${error.message ?: url}")
        } catch (error: IOException) {
            throw EngineException(error.message ?: error.javaClass.simpleName)
        } finally {
            connection.disconnect()
        }
    }

    private fun describeError(body: String, status: Int, reason: String?): String {
        if (body.isNotBlank()) {
            try {
                val json = JSONObject(body)
                for (key in listOf("error", "detail", "message")) {
                    val value = json.optString(key, "")
                    if (value.isNotBlank()) return "HTTP $status · $value"
                }
            } catch (ignored: JSONException) {
                // Corps non JSON : on retombe sur le libellé HTTP.
            }
        }
        val suffix = reason?.takeIf { it.isNotBlank() }?.let { " · $it" } ?: ""
        return "HTTP $status$suffix"
    }

    private fun parseObject(body: String, origin: String): JSONObject {
        if (body.isBlank()) throw EngineException("réponse vide de $origin")
        return try {
            JSONObject(body)
        } catch (error: JSONException) {
            throw EngineException("réponse illisible de $origin : ${error.message}")
        }
    }

    private fun parseAnalysis(json: JSONObject): Analysis {
        val grid = json.optJSONObject("grid")
        val timing = json.optJSONObject("timing")
        return Analysis(
            ok = json.optBoolean("ok", false),
            message = json.optString("message", ""),
            move = json.optJSONObject("move")?.let { parseMove(it) },
            rows = grid?.optInt("rows", 0) ?: 0,
            cols = grid?.optInt("cols", 0) ?: 0,
            confidence = json.optDouble("confidence", 0.0).toFloat(),
            pieces = json.optInt("pieces", 0),
            blocks = json.optInt("blocks", 0),
            holes = json.optInt("holes", 0),
            specials = json.optInt("specials", 0),
            settleMs = json.optInt("settle_ms", 1_250),
            captureIntervalMs = json.optInt("capture_interval_ms", 1_000),
            stale = json.optBoolean("stale", false),
            solverMode = json.optString("solver_mode", ""),
            engineVersion = json.optString("engine_version", ""),
            serverTotalMs = (timing?.optDouble("total_ms", 0.0) ?: 0.0).toInt(),
        )
    }

    private fun parseMove(json: JSONObject): Move? {
        val source = json.optJSONObject("from") ?: return null
        val target = json.optJSONObject("to")
        return Move(
            kind = json.optString("kind", "swipe"),
            from = point(source),
            to = target?.let { point(it) },
            durationMs = json.optInt("duration_ms", 180),
            score = json.optDouble("score", 0.0).toFloat(),
            reason = json.optString("reason", ""),
            combo = json.optBoolean("combo", false),
        )
    }

    private fun point(json: JSONObject) = Point(
        x = json.optDouble("x", 0.0).toFloat(),
        y = json.optDouble("y", 0.0).toFloat(),
    )

    /**
     * Construit le champ `config` de la requête : la configuration versionnée
     * récupérée sur GitHub, dans laquelle le mode choisi dans l'application a
     * priorité.
     */
    private fun buildConfigField(override: JSONObject?, solverMode: String): String? {
        val merged = override?.let { JSONObject(it.toString()) } ?: JSONObject()
        if (solverMode.isNotBlank()) {
            val solver = merged.optJSONObject("solver") ?: JSONObject()
            solver.put("mode", solverMode)
            merged.put("solver", solver)
        }
        return if (merged.length() == 0) null else merged.toString()
    }

    private fun buildMultipart(
        boundary: String,
        fields: List<Pair<String, String>>,
        image: ByteArray,
    ): ByteArray {
        val output = ByteArrayOutputStream(image.size + 2_048)
        for ((name, value) in fields) {
            writeAscii(output, "--$boundary\r\n")
            writeAscii(output, "Content-Disposition: form-data; name=\"$name\"\r\n\r\n")
            output.write(value.toByteArray(StandardCharsets.UTF_8))
            writeAscii(output, "\r\n")
        }
        writeAscii(output, "--$boundary\r\n")
        writeAscii(output, "Content-Disposition: form-data; name=\"image\"; filename=\"capture.jpg\"\r\n")
        writeAscii(output, "Content-Type: image/jpeg\r\n\r\n")
        output.write(image)
        writeAscii(output, "\r\n--$boundary--\r\n")
        return output.toByteArray()
    }

    private fun writeAscii(output: ByteArrayOutputStream, text: String) {
        output.write(text.toByteArray(StandardCharsets.US_ASCII))
    }
}
