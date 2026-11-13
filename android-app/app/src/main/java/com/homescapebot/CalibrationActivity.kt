package com.homescapebot

import android.graphics.Bitmap
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import kotlin.math.min
import kotlin.math.roundToInt

/** An in-memory screenshot is used only for calibration and discarded when the screen closes. */
object CalibrationSession { var screenshot: Bitmap? = null }

class CalibrationActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val bitmap = CalibrationSession.screenshot ?: run { finish(); return }
        setContent {
            MaterialTheme { CalibrationScreen(bitmap) { finish() } }
        }
    }

    override fun onDestroy() {
        if (!isChangingConfigurations) {
            CalibrationSession.screenshot?.recycle()
            CalibrationSession.screenshot = null
            FloatingOverlayService.instance?.showBubble()
        }
        super.onDestroy()
    }

    @Composable
    private fun CalibrationScreen(bitmap: Bitmap, done: () -> Unit) {
        var rows by remember { mutableIntStateOf(9) }
        var cols by remember { mutableIntStateOf(9) }
        var first by remember { mutableStateOf<Offset?>(null) }
        var last by remember { mutableStateOf<Offset?>(null) }
        val templates = remember { mutableStateListOf<FloatArray>() }
        var notice by remember { mutableStateOf("") }
        val draft = remember(first, last, rows, cols, templates.size) {
            val a = first; val b = last
            if (a == null || b == null) null else {
                val dx = (b.x - a.x) / (cols - 1); val dy = (b.y - a.y) / (rows - 1)
                Calibration(bitmap.width, bitmap.height, rows, cols, a.x - dx / 2, a.y - dy / 2, b.x + dx / 2, b.y + dy / 2, templates.toList())
                    .takeIf { dx > 0 && dy > 0 && it.left >= 0 && it.top >= 0 && it.right <= 1 && it.bottom <= 1 }
            }
        }
        val board = remember(draft) { draft?.let { LocalEngine.board(bitmap, it) } }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Calibrer le plateau", style = MaterialTheme.typography.headlineSmall)
            Text(when {
                first == null -> "Choisissez lignes et colonnes, puis touchez le centre de la case en haut à gauche."
                last == null -> "Touchez maintenant le centre de la case en bas à droite."
                else -> "Touchez une pièce de chaque couleur différente. Vérifiez les numéros : une même couleur doit avoir le même numéro ; ? signifie inconnu."
            })
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Lignes : $rows")
                TextButton(onClick = { rows = (rows - 1).coerceAtLeast(3); first = null; last = null; templates.clear() }) { Text("−") }
                TextButton(onClick = { rows = (rows + 1).coerceAtMost(15); first = null; last = null; templates.clear() }) { Text("+") }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Colonnes : $cols")
                TextButton(onClick = { cols = (cols - 1).coerceAtLeast(3); first = null; last = null; templates.clear() }) { Text("−") }
                TextButton(onClick = { cols = (cols + 1).coerceAtMost(15); first = null; last = null; templates.clear() }) { Text("+") }
            }
            val image = remember(bitmap) { bitmap.asImageBitmap() }
            Canvas(Modifier.fillMaxWidth().height(400.dp).pointerInput(first, last, rows, cols, draft) {
                detectTapGestures { tap ->
                    val scale = min(size.width.toFloat() / bitmap.width, size.height.toFloat() / bitmap.height)
                    val ox = (size.width - bitmap.width * scale) / 2f; val oy = (size.height - bitmap.height * scale) / 2f
                    val p = Offset((tap.x - ox) / (bitmap.width * scale), (tap.y - oy) / (bitmap.height * scale))
                    if (p.x !in 0f..1f || p.y !in 0f..1f) return@detectTapGestures
                    when {
                        first == null -> { first = p; notice = "" }
                        last == null -> {
                            val a = first!!; val dx = (p.x - a.x) / (cols - 1); val dy = (p.y - a.y) / (rows - 1)
                            if (dx <= 0 || dy <= 0 || a.x - dx / 2 < 0 || a.y - dy / 2 < 0 || p.x + dx / 2 > 1 || p.y + dy / 2 > 1) notice = "Cases invalides : choisissez les centres haut-gauche puis bas-droite."
                            else { last = p; notice = "" }
                        }
                        draft != null -> {
                            val col = ((p.x - draft.left) / (draft.right - draft.left) * cols).toInt()
                            val row = ((p.y - draft.top) / (draft.bottom - draft.top) * rows).toInt()
                            if (p.x < draft.left || p.x >= draft.right || p.y < draft.top || p.y >= draft.bottom || row !in 0 until rows || col !in 0 until cols) {
                                notice = "Touchez une case dans la grille."
                            } else {
                                val sample = LocalEngine.sample(bitmap, draft, row, col)
                                when {
                                    sample == null -> notice = "Couleur trop peu visible : essayez une autre pièce."
                                    templates.size >= 10 -> notice = "Maximum 10 couleurs."
                                    templates.any { LocalEngine.similarity(it, sample) > .70f } -> notice = "Cette couleur ressemble à une couleur déjà choisie."
                                    else -> { templates.add(sample); notice = "Couleur ${templates.size} ajoutée." }
                                }
                            }
                        }
                    }
                }
            }) {
                val scale = min(size.width / bitmap.width, size.height / bitmap.height)
                val w = bitmap.width * scale; val h = bitmap.height * scale
                val ox = (size.width - w) / 2f; val oy = (size.height - h) / 2f
                drawImage(image, dstOffset = IntOffset(ox.roundToInt(), oy.roundToInt()), dstSize = IntSize(w.roundToInt(), h.roundToInt()))
                first?.let { drawCircle(Color.Yellow, 8.dp.toPx(), Offset(ox + it.x * w, oy + it.y * h)) }
                last?.let { drawCircle(Color.Yellow, 8.dp.toPx(), Offset(ox + it.x * w, oy + it.y * h)) }
                if (draft != null) {
                    for (c in 0..cols) {
                        val x = ox + (draft.left + (draft.right - draft.left) * c / cols) * w
                        drawLine(Color.Yellow, Offset(x, oy + draft.top * h), Offset(x, oy + draft.bottom * h), 1.dp.toPx())
                    }
                    for (r in 0..rows) {
                        val y = oy + (draft.top + (draft.bottom - draft.top) * r / rows) * h
                        drawLine(Color.Yellow, Offset(ox + draft.left * w, y), Offset(ox + draft.right * w, y), 1.dp.toPx())
                    }
                    if (board != null && templates.isNotEmpty()) {
                        drawIntoCanvas { canvas ->
                            val paint = android.graphics.Paint().apply { color = android.graphics.Color.WHITE; textSize = 12.dp.toPx(); isAntiAlias = true; setShadowLayer(3f, 0f, 0f, android.graphics.Color.BLACK) }
                            for (r in 0 until rows) for (c in 0 until cols) {
                                val x = ox + (draft.left + (c + .25f) * (draft.right - draft.left) / cols) * w
                                val y = oy + (draft.top + (r + .7f) * (draft.bottom - draft.top) / rows) * h
                                canvas.nativeCanvas.drawText(board[r][c]?.let { "${it + 1}" } ?: "?", x, y, paint)
                            }
                        }
                    }
                }
            }
            Text("Couleurs : ${templates.size} / 10. ${if (board != null) "Cases reconnues : ${board.sumOf { row -> row.count { it != null } }}/${rows * cols}." else ""}")
            if (notice.isNotEmpty()) Text(notice)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { first = null; last = null; templates.clear(); notice = "" }) { Text("Recommencer") }
                Button(onClick = {
                    if (draft == null || !draft.valid()) notice = "Choisissez la grille et au moins trois couleurs différentes."
                    else { draft.save(this@CalibrationActivity); done() }
                }) { Text("Enregistrer") }
            }
            Text("N'utilisez la lecture que si les cases et couleurs reconnues sont correctes. Les obstacles peuvent être confondus avec des pièces.", style = MaterialTheme.typography.bodySmall)
        }
    }
}
