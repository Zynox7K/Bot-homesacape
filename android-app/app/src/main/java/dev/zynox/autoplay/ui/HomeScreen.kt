package dev.zynox.autoplay.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.zynox.autoplay.ApiClient
import dev.zynox.autoplay.BotState
import dev.zynox.autoplay.ConfigSync
import dev.zynox.autoplay.FloatingOverlayService
import dev.zynox.autoplay.LogEntry
import dev.zynox.autoplay.LogLevel
import dev.zynox.autoplay.R
import dev.zynox.autoplay.RuntimeBus
import dev.zynox.autoplay.RuntimeSnapshot
import dev.zynox.autoplay.SettingsRepository
import dev.zynox.autoplay.describeError
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private const val PERMISSION_POLL_MS = 700L
private const val SAVE_DELAY_MS = 500L

/**
 * Écran unique de l'application : appairage du moteur, autorisations, pilotage,
 * réglages et journal.
 *
 * Chaque section gère ses propres champs et les écrit dans [SettingsRepository].
 * L'écran ne conserve qu'un état de coordination : les autorisations, la présence
 * du service et le compteur de révision qui fait relire les réglages après une
 * synchronisation du dépôt.
 */
@Composable
fun HomeScreen(settings: SettingsRepository) {
    val context = LocalContext.current
    val snapshot by RuntimeBus.snapshot.collectAsState()
    val entries by RuntimeBus.entries.collectAsState()

    var permissions by remember { mutableStateOf(PermissionStatus.read(context)) }
    var serviceAlive by remember { mutableStateOf(FloatingOverlayService.isRunning) }
    var settingsRevision by remember { mutableIntStateOf(0) }

    val notificationLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
    ) { granted ->
        permissions = PermissionStatus.read(context)
        if (!granted) {
            RuntimeBus.log(
                context.getString(R.string.log_notifications_denied),
                LogLevel.WARNING,
            )
        }
    }

    LaunchedEffect(Unit) {
        while (true) {
            permissions = PermissionStatus.read(context)
            serviceAlive = FloatingOverlayService.isRunning
            delay(PERMISSION_POLL_MS)
        }
    }

    val requestNotifications: () -> Unit = {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            context.openNotificationSettings()
        }
    }

    val startAssistant: () -> Unit = {
        when {
            !permissions.overlay -> {
                RuntimeBus.log(context.getString(R.string.error_overlay_missing), LogLevel.ERROR)
                context.openOverlaySettings()
            }

            !permissions.accessibility -> {
                RuntimeBus.log(context.getString(R.string.error_accessibility_missing), LogLevel.ERROR)
                context.openAccessibilitySettings()
            }

            settings.engineUrl.isBlank() -> {
                RuntimeBus.log(context.getString(R.string.error_engine_missing), LogLevel.ERROR)
            }

            else -> {
                if (!permissions.notifications) requestNotifications()
                FloatingOverlayService.start(context)
                serviceAlive = true
            }
        }
    }

    Scaffold(containerColor = DarkBackground) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp)
                .padding(top = 24.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            HeaderBlock(
                snapshot = snapshot,
                serviceAlive = serviceAlive,
                canPilot = permissions.canPilot,
            )

            ControlCard(
                snapshot = snapshot,
                serviceAlive = serviceAlive,
                onStart = startAssistant,
                onToggle = { FloatingOverlayService.toggle(context) },
                onStop = {
                    FloatingOverlayService.stop(context)
                    serviceAlive = false
                },
            )

            PermissionsCard(
                permissions = permissions,
                onAccessibility = { context.openAccessibilitySettings() },
                onOverlay = { context.openOverlaySettings() },
                onNotifications = requestNotifications,
            )

            EngineCard(settings = settings)

            GitHubCard(
                settings = settings,
                onConfigurationApplied = { settingsRevision += 1 },
            )

            SettingsForm(settings = settings, revision = settingsRevision)

            LogCard(entries = entries, onClear = { RuntimeBus.clearLog() })

            Text(
                text = stringResource(R.string.bubble_hint),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
        }
    }
}

// ---------------------------------------------------------------------- //
// En-tête
// ---------------------------------------------------------------------- #

@Composable
private fun HeaderBlock(snapshot: RuntimeSnapshot, serviceAlive: Boolean, canPilot: Boolean) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            StatusDot(
                when {
                    snapshot.state == BotState.ERROR -> DangerRed
                    serviceAlive && snapshot.playing -> BrandGreen
                    canPilot -> WarningOrange
                    else -> TextSecondary
                },
            )
            Text(
                text = stringResource(R.string.app_name),
                fontSize = 26.sp,
                fontWeight = FontWeight.Bold,
                color = TextPrimary,
            )
        }
        Text(
            text = stringResource(R.string.app_tagline),
            style = MaterialTheme.typography.bodyMedium,
            color = BrandGreen,
            fontWeight = FontWeight.Medium,
        )
        Text(
            text = stringResource(R.string.app_intro),
            style = MaterialTheme.typography.bodySmall,
            color = TextSecondary,
        )
    }
}

// ---------------------------------------------------------------------- //
// Pilotage
// ---------------------------------------------------------------------- #

@Composable
private fun ControlCard(
    snapshot: RuntimeSnapshot,
    serviceAlive: Boolean,
    onStart: () -> Unit,
    onToggle: () -> Unit,
    onStop: () -> Unit,
) {
    SectionCard(title = stringResource(R.string.control_title)) {
        MetricRow(
            label = stringResource(R.string.control_state),
            value = stateLabel(snapshot),
            valueColor = stateColor(snapshot),
        )
        MetricRow(
            label = stringResource(R.string.control_moves),
            value = stringResource(R.string.format_moves, snapshot.movesPlayed),
        )
        MetricRow(
            label = stringResource(R.string.control_grid),
            value = gridLabel(snapshot),
        )
        MetricRow(
            label = stringResource(R.string.control_score),
            value = if (snapshot.lastScore > 0f) {
                stringResource(R.string.format_score, snapshot.lastScore)
            } else {
                stringResource(R.string.control_none)
            },
        )
        MetricRow(
            label = stringResource(R.string.control_roundtrip),
            value = if (snapshot.lastRoundTripMs > 0L) {
                stringResource(R.string.format_ms, snapshot.lastRoundTripMs.toInt())
            } else {
                stringResource(R.string.control_none)
            },
        )
        MetricRow(
            label = stringResource(R.string.control_engine),
            value = engineLabel(snapshot),
        )
        MetricRow(
            label = stringResource(R.string.control_last_move),
            value = snapshot.lastReason.ifBlank { stringResource(R.string.control_none) },
        )

        if (snapshot.message.isNotBlank()) {
            Text(
                text = snapshot.message,
                style = MaterialTheme.typography.bodySmall,
                color = DangerRed,
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (serviceAlive) {
                Button(
                    onClick = onToggle,
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = BrandGreen,
                        contentColor = Color(0xFF06130B),
                    ),
                ) {
                    Text(
                        text = if (snapshot.playing) {
                            stringResource(R.string.control_pause)
                        } else {
                            stringResource(R.string.control_resume)
                        },
                    )
                }
                OutlinedButton(onClick = onStop, modifier = Modifier.weight(1f)) {
                    Text(
                        text = stringResource(R.string.control_stop),
                        color = TextSecondary,
                    )
                }
            } else {
                Button(
                    onClick = onStart,
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = BrandGreen,
                        contentColor = Color(0xFF06130B),
                    ),
                ) {
                    Text(stringResource(R.string.control_start))
                }
            }
        }

        if (serviceAlive) {
            Text(
                text = stringResource(R.string.control_running_hint),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
        }
    }
}

@Composable
private fun stateLabel(snapshot: RuntimeSnapshot): String = stringResource(
    when (snapshot.state) {
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

private fun stateColor(snapshot: RuntimeSnapshot): Color = when (snapshot.state) {
    BotState.PLAYING, BotState.CAPTURING, BotState.THINKING, BotState.WAITING -> BrandGreen
    BotState.DRY_RUN -> AccentBlue
    BotState.PAUSED -> WarningOrange
    BotState.ERROR -> DangerRed
    BotState.IDLE, BotState.STOPPED -> TextSecondary
}

/** Taille de la grille détectée, complétée par la fiabilité de la détection. */
@Composable
private fun gridLabel(snapshot: RuntimeSnapshot): String {
    if (snapshot.gridDescription.isBlank()) return stringResource(R.string.control_none)
    if (snapshot.confidence <= 0f) return snapshot.gridDescription
    val confidence = stringResource(R.string.format_confidence, snapshot.confidence * 100f)
    return snapshot.gridDescription + " · " + confidence
}

@Composable
private fun engineLabel(snapshot: RuntimeSnapshot): String {
    val none = stringResource(R.string.control_none)
    val version = snapshot.engineVersion
    val mode = snapshot.solverMode
    return when {
        version.isNotBlank() && mode.isNotBlank() -> "$version · $mode"
        version.isNotBlank() -> version
        mode.isNotBlank() -> mode
        else -> none
    }
}

// ---------------------------------------------------------------------- //
// Autorisations
// ---------------------------------------------------------------------- #

@Composable
private fun PermissionsCard(
    permissions: PermissionStatus,
    onAccessibility: () -> Unit,
    onOverlay: () -> Unit,
    onNotifications: () -> Unit,
) {
    val ready = stringResource(R.string.permission_state_ready)
    SectionCard(
        title = stringResource(R.string.permissions_title),
        subtitle = stringResource(R.string.permissions_subtitle),
    ) {
        PermissionRow(
            title = stringResource(R.string.permission_accessibility_title),
            detail = stringResource(R.string.permission_accessibility_detail),
            granted = permissions.accessibility,
            actionLabel = stringResource(R.string.permission_action_enable),
            readyLabel = ready,
            onAction = onAccessibility,
        )
        PermissionRow(
            title = stringResource(R.string.permission_overlay_title),
            detail = stringResource(R.string.permission_overlay_detail),
            granted = permissions.overlay,
            actionLabel = stringResource(R.string.permission_action_grant),
            readyLabel = ready,
            onAction = onOverlay,
        )
        PermissionRow(
            title = stringResource(R.string.permission_notifications_title),
            detail = stringResource(R.string.permission_notifications_detail),
            granted = permissions.notifications,
            actionLabel = stringResource(R.string.permission_action_grant),
            readyLabel = ready,
            onAction = onNotifications,
        )
        if (!permissions.accessibility) {
            Text(
                text = stringResource(R.string.permission_accessibility_hint),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
        }
    }
}

// ---------------------------------------------------------------------- //
// Moteur distant
// ---------------------------------------------------------------------- #

/** Résultat du test de connexion au moteur. */
private sealed interface EngineCheck {
    data object Unknown : EngineCheck
    data object Testing : EngineCheck
    data class Ok(val health: ApiClient.Health) : EngineCheck
    data class Failed(val message: String) : EngineCheck
}

@Composable
private fun EngineCard(settings: SettingsRepository) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(settings.engineUrl) }
    var check by remember { mutableStateOf<EngineCheck>(EngineCheck.Unknown) }

    SectionCard(
        title = stringResource(R.string.engine_title),
        subtitle = stringResource(R.string.engine_subtitle),
    ) {
        OutlinedTextField(
            value = url,
            onValueChange = {
                url = it
                settings.engineUrl = it
            },
            modifier = Modifier.fillMaxWidth(),
            label = { Text(stringResource(R.string.engine_url_label)) },
            placeholder = { Text(stringResource(R.string.engine_url_hint)) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Uri,
                imeAction = ImeAction.Done,
            ),
            colors = fieldColors(),
        )

        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                onClick = {
                    settings.engineUrl = url
                    scope.launch {
                        if (url.isBlank()) {
                            check = EngineCheck.Failed(context.getString(R.string.error_engine_missing))
                            return@launch
                        }
                        check = EngineCheck.Testing
                        check = try {
                            EngineCheck.Ok(
                                withContext(Dispatchers.IO) { ApiClient.health(url) },
                            )
                        } catch (error: Exception) {
                            EngineCheck.Failed(describeError(error))
                        }
                    }
                },
                colors = ButtonDefaults.buttonColors(
                    containerColor = BrandGreen,
                    contentColor = Color(0xFF06130B),
                ),
            ) {
                Text(stringResource(R.string.engine_test))
            }
            if (check is EngineCheck.Testing) {
                CircularProgressIndicator(
                    modifier = Modifier.padding(4.dp),
                    color = BrandGreen,
                    strokeWidth = 2.dp,
                )
            }
        }

        when (val current = check) {
            is EngineCheck.Unknown -> Text(
                text = stringResource(R.string.engine_state_unknown),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )

            is EngineCheck.Testing -> Text(
                text = stringResource(R.string.engine_state_testing),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )

            is EngineCheck.Ok -> {
                Text(
                    text = stringResource(
                        R.string.engine_state_ok,
                        current.health.version,
                        current.health.solverMode,
                        current.health.latencyMs.toInt(),
                    ),
                    style = MaterialTheme.typography.bodySmall,
                    color = BrandGreen,
                )
                Text(
                    text = stringResource(
                        R.string.engine_state_details,
                        current.health.templates,
                        current.health.uptimeSeconds,
                    ),
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary,
                )
            }

            is EngineCheck.Failed -> Text(
                text = stringResource(R.string.engine_state_error, current.message),
                style = MaterialTheme.typography.bodySmall,
                color = DangerRed,
            )
        }
    }
}

// ---------------------------------------------------------------------- //
// Dépôt GitHub
// ---------------------------------------------------------------------- #

@Composable
private fun GitHubCard(
    settings: SettingsRepository,
    onConfigurationApplied: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var repoUrl by remember { mutableStateOf(settings.repoUrl) }
    var token by remember { mutableStateOf(settings.githubToken) }
    var adminKey by remember { mutableStateOf(settings.adminKey) }
    var autoSync by remember { mutableStateOf(settings.autoSync) }
    var message by remember { mutableStateOf<String?>(null) }
    var messageIsError by remember { mutableStateOf(false) }
    var running by remember { mutableStateOf(false) }


    SectionCard(
        title = stringResource(R.string.github_title),
        subtitle = stringResource(R.string.github_subtitle),
    ) {
        OutlinedTextField(
            value = repoUrl,
            onValueChange = {
                repoUrl = it
                settings.repoUrl = it
            },
            modifier = Modifier.fillMaxWidth(),
            label = { Text(stringResource(R.string.github_repo_label)) },
            placeholder = { Text(stringResource(R.string.github_repo_hint)) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Uri,
                imeAction = ImeAction.Next,
            ),
            colors = fieldColors(),
        )
        OutlinedTextField(
            value = token,
            onValueChange = {
                token = it
                settings.githubToken = it
            },
            modifier = Modifier.fillMaxWidth(),
            label = { Text(stringResource(R.string.github_token_label)) },
            placeholder = { Text(stringResource(R.string.github_token_hint)) },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Password,
                imeAction = ImeAction.Next,
            ),
            colors = fieldColors(),
        )
        OutlinedTextField(
            value = adminKey,
            onValueChange = {
                adminKey = it
                settings.adminKey = it
            },
            modifier = Modifier.fillMaxWidth(),
            label = { Text(stringResource(R.string.github_admin_label)) },
            placeholder = { Text(stringResource(R.string.github_admin_hint)) },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Password,
                imeAction = ImeAction.Done,
            ),
            colors = fieldColors(),
        )

        SettingSwitch(
            label = stringResource(R.string.github_autosync),
            hint = null,
            checked = autoSync,
            onCheckedChange = {
                autoSync = it
                settings.autoSync = it
            },
        )

        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                onClick = {
                    settings.repoUrl = repoUrl
                    settings.githubToken = token
                    settings.adminKey = adminKey
                    scope.launch {
                        running = true
                        message = context.getString(R.string.github_sync_running)
                        messageIsError = false
                        val outcome = ConfigSync.synchronize(context, settings)
                        message = outcome.message
                        messageIsError = !outcome.ok
                        RuntimeBus.log(
                            outcome.message,
                            if (outcome.ok) LogLevel.SUCCESS else LogLevel.WARNING,
                        )
                        if (outcome.ok) {
                            val mode = outcome.solverMode
                            if (mode in SettingsRepository.SOLVER_MODES) settings.solverMode = mode
                            onConfigurationApplied()
                        }
                        running = false
                    }
                },
                enabled = !running,
                colors = ButtonDefaults.buttonColors(
                    containerColor = BrandGreen,
                    contentColor = Color(0xFF06130B),
                    disabledContainerColor = DarkStroke,
                    disabledContentColor = TextSecondary,
                ),
            ) {
                Text(stringResource(R.string.github_sync))
            }
            if (running) {
                CircularProgressIndicator(color = BrandGreen, strokeWidth = 2.dp)
            }
        }

        val current = message
        if (current != null) {
            Text(
                text = current,
                style = MaterialTheme.typography.bodySmall,
                color = if (messageIsError) DangerRed else BrandGreen,
            )
        }
    }
}

// ---------------------------------------------------------------------- //
// Réglages
// ---------------------------------------------------------------------- #

@Composable
private fun SettingsForm(settings: SettingsRepository, revision: Int) {
    var solverMode by remember { mutableStateOf(settings.solverMode) }
    var captureInterval by remember { mutableStateOf(settings.captureIntervalMs.toFloat()) }
    var jpegQuality by remember { mutableStateOf(settings.jpegQuality.toFloat()) }
    var analysisWidth by remember { mutableStateOf(settings.analysisWidth.toFloat()) }
    var gestureScale by remember { mutableStateOf(settings.gestureScale) }
    var requestTimeout by remember { mutableStateOf(settings.requestTimeoutMs.toFloat()) }
    var goalColor by remember { mutableIntStateOf(settings.goalColor) }
    var targetPackage by remember { mutableStateOf(settings.targetPackage) }
    var pauseOffGame by remember { mutableStateOf(settings.pauseOffGame) }
    var dryRun by remember { mutableStateOf(settings.dryRun) }

    // Une synchronisation du dépôt peut imposer un autre mode de calcul.
    LaunchedEffect(revision) {
        if (revision > 0) solverMode = settings.solverMode
    }

    LaunchedEffect(captureInterval) {
        delay(SAVE_DELAY_MS)
        settings.captureIntervalMs = captureInterval.toInt()
    }
    LaunchedEffect(jpegQuality) {
        delay(SAVE_DELAY_MS)
        settings.jpegQuality = jpegQuality.toInt()
    }
    LaunchedEffect(analysisWidth) {
        delay(SAVE_DELAY_MS)
        settings.analysisWidth = analysisWidth.toInt()
    }
    LaunchedEffect(gestureScale) {
        delay(SAVE_DELAY_MS)
        settings.gestureScale = gestureScale
    }
    LaunchedEffect(requestTimeout) {
        delay(SAVE_DELAY_MS)
        settings.requestTimeoutMs = requestTimeout.toInt()
    }

    val modeHint = stringResource(
        when (solverMode) {
            SettingsRepository.MODE_FAST -> R.string.settings_mode_hint_fast
            SettingsRepository.MODE_BALANCED -> R.string.settings_mode_hint_balanced
            else -> R.string.settings_mode_hint_expert
        },
    )

    SectionCard(title = stringResource(R.string.settings_title)) {
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                text = stringResource(R.string.settings_mode),
                style = MaterialTheme.typography.bodyMedium,
                color = TextPrimary,
            )
            ModeSelector(
                options = listOf(
                    SettingsRepository.MODE_FAST to stringResource(R.string.settings_mode_fast),
                    SettingsRepository.MODE_BALANCED to stringResource(R.string.settings_mode_balanced),
                    SettingsRepository.MODE_EXPERT to stringResource(R.string.settings_mode_expert),
                ),
                selected = solverMode,
                onSelect = {
                    solverMode = it
                    settings.solverMode = it
                },
            )
            Text(
                text = modeHint,
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
        }

        SettingSlider(
            label = stringResource(R.string.settings_interval),
            valueLabel = stringResource(R.string.format_ms, captureInterval.toInt()),
            value = captureInterval,
            valueRange = 1_000f..4_000f,
            steps = 5,
            hint = stringResource(R.string.settings_interval_hint),
            onValueChange = { captureInterval = it },
        )
        SettingSlider(
            label = stringResource(R.string.settings_quality),
            valueLabel = stringResource(R.string.format_percent, jpegQuality.toInt()),
            value = jpegQuality,
            valueRange = 40f..95f,
            steps = 10,
            hint = stringResource(R.string.settings_quality_hint),
            onValueChange = { jpegQuality = it },
        )
        SettingSlider(
            label = stringResource(R.string.settings_width),
            valueLabel = stringResource(R.string.format_pixels, analysisWidth.toInt()),
            value = analysisWidth,
            valueRange = 320f..880f,
            steps = 13,
            hint = null,
            onValueChange = { analysisWidth = it },
        )
        SettingSlider(
            label = stringResource(R.string.settings_gesture),
            valueLabel = stringResource(R.string.format_scale, gestureScale),
            value = gestureScale,
            valueRange = 0.5f..2.5f,
            steps = 7,
            hint = null,
            onValueChange = { gestureScale = it },
        )
        SettingSlider(
            label = stringResource(R.string.settings_timeout),
            valueLabel = stringResource(R.string.format_seconds, requestTimeout / 1_000f),
            value = requestTimeout,
            valueRange = 2_000f..20_000f,
            steps = 8,
            hint = null,
            onValueChange = { requestTimeout = it },
        )

        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = stringResource(R.string.settings_goal_color),
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextPrimary,
                )
                Text(
                    text = if (goalColor < 0) {
                        stringResource(R.string.settings_goal_auto)
                    } else {
                        stringResource(R.string.format_goal_color, goalColor)
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = TextSecondary,
                )
            }
            OutlinedButton(
                onClick = {
                    goalColor = if (goalColor >= 7) -1 else goalColor + 1
                    settings.goalColor = goalColor
                },
            ) {
                Text(stringResource(R.string.settings_goal_change), color = TextSecondary)
            }
        }

        OutlinedTextField(
            value = targetPackage,
            onValueChange = {
                targetPackage = it
                settings.targetPackage = it
            },
            modifier = Modifier.fillMaxWidth(),
            label = { Text(stringResource(R.string.settings_target_package)) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            colors = fieldColors(),
        )

        SettingSwitch(
            label = stringResource(R.string.settings_pause_off_game),
            hint = null,
            checked = pauseOffGame,
            onCheckedChange = {
                pauseOffGame = it
                settings.pauseOffGame = it
            },
        )
        SettingSwitch(
            label = stringResource(R.string.settings_dry_run),
            hint = stringResource(R.string.settings_dry_run_hint),
            checked = dryRun,
            onCheckedChange = {
                dryRun = it
                settings.dryRun = it
            },
        )
    }
}

// ---------------------------------------------------------------------- //
// Journal
// ---------------------------------------------------------------------- #

@Composable
private fun LogCard(entries: List<LogEntry>, onClear: () -> Unit) {
    val timeFormat = remember { SimpleDateFormat("HH:mm:ss", Locale.getDefault()) }
    SectionCard(title = stringResource(R.string.log_title)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = stringResource(R.string.format_moves, entries.size),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
            TextButton(onClick = onClear) {
                Text(stringResource(R.string.log_clear), color = BrandGreen)
            }
        }

        if (entries.isEmpty()) {
            Text(
                text = stringResource(R.string.log_empty),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
            return@sectionCard
        }

        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            for (entry in entries.take(MAX_VISIBLE_ENTRIES)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        text = timeFormat.format(Date(entry.timeMs)),
                        style = MaterialTheme.typography.labelSmall,
                        color = TextSecondary,
                        fontFamily = FontFamily.Monospace,
                    )
                    Text(
                        text = entry.message,
                        style = MaterialTheme.typography.labelSmall,
                        color = levelColor(entry.level),
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}

private const val MAX_VISIBLE_ENTRIES = 14

private fun levelColor(level: LogLevel): Color = when (level) {
    LogLevel.SUCCESS -> BrandGreen
    LogLevel.WARNING -> WarningOrange
    LogLevel.ERROR -> DangerRed
    LogLevel.INFO -> TextPrimary
}

@Composable
private fun fieldColors() = OutlinedTextFieldDefaults.colors(
    focusedTextColor = TextPrimary,
    unfocusedTextColor = TextPrimary,
    focusedBorderColor = BrandGreen,
    unfocusedBorderColor = DarkStroke,
    focusedContainerColor = DarkSurface,
    unfocusedContainerColor = DarkSurface,
    cursorColor = BrandGreen,
    focusedLabelColor = BrandGreen,
    unfocusedLabelColor = TextSecondary,
)
