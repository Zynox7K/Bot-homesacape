package dev.zynox.autoplay

/**
 * Message lisible pour une exception remontée à l'utilisateur.
 *
 * Les erreurs de transport sont déjà reformulées en français par
 * [ApiClient.EngineException] ; pour toute autre exception, on retombe sur le nom
 * de la classe plutôt que sur un `null` inexploitable.
 */
internal fun describeError(error: Throwable): String =
    error.message?.takeIf { it.isNotBlank() } ?: error.javaClass.simpleName
