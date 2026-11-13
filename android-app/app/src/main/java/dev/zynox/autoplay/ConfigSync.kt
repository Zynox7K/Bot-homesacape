package dev.zynox.autoplay

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** Résultat d'une synchronisation de configuration. */
data class SyncOutcome(
    val ok: Boolean,
    val message: String,
    val solverMode: String = "",
    val pushed: Boolean = false,
)

/**
 * Synchronisation de la configuration entre le dépôt GitHub et le moteur.
 *
 * Le fichier `bot-engine/config.json` est la source de vérité : il est versionné,
 * revu dans les pull requests et déployé avec le service. L'application le
 * récupère via l'API GitHub, le conserve localement puis, si la clé
 * d'administration est renseignée, l'applique à chaud sur le moteur.
 *
 * Même sans clé d'administration, la configuration récupérée est renvoyée à
 * chaque analyse : le téléphone joue alors avec les réglages du dépôt, et un
 * simple `git push` suffit à changer son comportement.
 */
object ConfigSync {

    suspend fun synchronize(context: Context, settings: SettingsRepository): SyncOutcome =
        withContext(Dispatchers.IO) {
            if (settings.repoUrl.isBlank() || ApiClient.parseRepo(settings.repoUrl) == null) {
                return@withContext SyncOutcome(false, context.getString(R.string.github_repo_invalid))
            }
            try {
                val config = ApiClient.fetchRepoConfig(settings.repoUrl, settings.githubToken)
                settings.repoConfig = config.toString()
                val mode = solverModeOf(config)
                val pushed = pushToEngine(context, settings, config)
                val message = if (pushed) {
                    context.getString(R.string.github_sync_pushed, mode)
                } else {
                    context.getString(R.string.github_sync_stored)
                }
                SyncOutcome(true, message, mode, pushed)
            } catch (error: Exception) {
                SyncOutcome(false, context.getString(R.string.github_sync_error, describeError(error)))
            }
        }

    /** Mode de calcul déclaré par la configuration du dépôt. */
    fun solverModeOf(config: JSONObject): String =
        config.optJSONObject("solver")?.optString("mode", "").orEmpty()

    private fun pushToEngine(
        context: Context,
        settings: SettingsRepository,
        config: JSONObject,
    ): Boolean {
        if (settings.adminKey.isBlank() || settings.engineUrl.isBlank()) return false
        return try {
            ApiClient.writeEngineConfig(settings.engineUrl, settings.adminKey, config)
            true
        } catch (error: Exception) {
            RuntimeBus.log(
                context.getString(R.string.github_push_failed, describeError(error)),
                LogLevel.WARNING,
            )
            false
        }
    }
}
