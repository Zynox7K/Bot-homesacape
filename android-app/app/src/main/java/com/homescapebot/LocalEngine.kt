package com.homescapebot

import android.graphics.Bitmap
import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max
import kotlin.math.min

data class Move(val x1: Float, val y1: Float, val x2: Float, val y2: Float)
data class Calibration(
    val width: Int, val height: Int, val rows: Int, val cols: Int,
    val left: Float, val top: Float, val right: Float, val bottom: Float,
    val templates: List<FloatArray>
) {
    fun valid(): Boolean = width > 0 && height > 0 && rows in 3..15 && cols in 3..15 &&
        left >= 0f && top >= 0f && right <= 1f && bottom <= 1f && left < right && top < bottom &&
        templates.size in 3..10 && templates.all { it.size == LocalEngine.BINS && it.all { v -> v.isFinite() && v >= 0f } && it.sum() > .99f && it.sum() < 1.01f }

    fun save(context: Context) {
        require(valid())
        val colors = JSONArray()
        templates.forEach { t -> colors.put(JSONArray().apply { t.forEach { put(it.toDouble()) } }) }
        val json = JSONObject().put("width", width).put("height", height).put("rows", rows).put("cols", cols)
            .put("left", left.toDouble()).put("top", top.toDouble()).put("right", right.toDouble()).put("bottom", bottom.toDouble()).put("templates", colors)
        context.getSharedPreferences("calibration", Context.MODE_PRIVATE).edit().putString("data", json.toString()).apply()
    }

    companion object {
        fun load(context: Context): Calibration? {
            return try {
            val raw = context.getSharedPreferences("calibration", Context.MODE_PRIVATE).getString("data", null) ?: return null
            val json = JSONObject(raw)
            val array = json.getJSONArray("templates")
            val templates = (0 until array.length()).map { i ->
                val template = array.getJSONArray(i)
                FloatArray(template.length()) { template.getDouble(it).toFloat() }
            }
            Calibration(json.getInt("width"), json.getInt("height"), json.getInt("rows"), json.getInt("cols"),
                json.getDouble("left").toFloat(), json.getDouble("top").toFloat(), json.getDouble("right").toFloat(), json.getDouble("bottom").toFloat(), templates).takeIf { it.valid() }
            } catch (_: Exception) { null }
        }
    }
}

/** Pure match-3 and color histogram logic; no OpenCV, network, or device-specific libraries. */
object LocalEngine {
    const val BINS = 24
    private const val SAMPLES = 7

    /** Calls pixel(x,y) only on a 7x7 sampling grid around the center of a tile. */
    fun signature(pixel: (Float, Float) -> Int, centerX: Float, centerY: Float, cellW: Float, cellH: Float): FloatArray? {
        val bins = FloatArray(BINS)
        var count = 0
        for (y in 0 until SAMPLES) for (x in 0 until SAMPLES) {
            val px = centerX + (x.toFloat() / (SAMPLES - 1) - .5f) * cellW * .60f
            val py = centerY + (y.toFloat() / (SAMPLES - 1) - .5f) * cellH * .60f
            val rgb = pixel(px, py)
            val r = (rgb shr 16 and 255).toFloat() / 255f
            val g = (rgb shr 8 and 255).toFloat() / 255f
            val b = (rgb and 255).toFloat() / 255f
            val high = max(r, max(g, b)); val low = min(r, min(g, b)); val delta = high - low
            if (high < .22f || delta < .18f || delta / high < .35f) continue
            var hue = when (high) {
                r -> ((g - b) / delta) % 6f
                g -> (b - r) / delta + 2f
                else -> (r - g) / delta + 4f
            }
            if (hue < 0) hue += 6f
            val bin = (hue / 6f * BINS).toInt().coerceIn(0, BINS - 1)
            // Smooth across hue-bin boundaries, including red across 0/360.
            bins[bin] += .6f; bins[(bin + BINS - 1) % BINS] += .2f; bins[(bin + 1) % BINS] += .2f
            count++
        }
        if (count < 8) return null
        for (i in bins.indices) bins[i] /= count
        return bins
    }

    fun similarity(a: FloatArray, b: FloatArray): Float = a.indices.sumOf { min(a[it], b[it]).toDouble() }.toFloat()

    fun classify(sample: FloatArray?, templates: List<FloatArray>): Int? {
        if (sample == null || templates.size < 2) return null
        val scores = templates.mapIndexed { i, template -> i to similarity(sample, template) }.sortedByDescending { it.second }
        return if (scores[0].second >= .55f && scores[0].second - scores[1].second >= .12f) scores[0].first else null
    }

    fun sample(bitmap: Bitmap, config: Calibration, row: Int, col: Int): FloatArray? {
        val cellW = (config.right - config.left) / config.cols
        val cellH = (config.bottom - config.top) / config.rows
        val cx = config.left + (col + .5f) * cellW
        val cy = config.top + (row + .5f) * cellH
        return signature({ x, y -> bitmap.getPixel((x * bitmap.width).toInt().coerceIn(0, bitmap.width - 1), (y * bitmap.height).toInt().coerceIn(0, bitmap.height - 1)) }, cx, cy, cellW, cellH)
    }

    fun board(bitmap: Bitmap, config: Calibration): List<List<Int?>> = (0 until config.rows).map { row ->
        (0 until config.cols).map { col -> classify(sample(bitmap, config, row, col), config.templates) }
    }

    fun matches(board: List<List<Int?>>): Set<Pair<Int, Int>> {
        val rows = board.size; val cols = board.first().size
        val found = mutableSetOf<Pair<Int, Int>>()
        for (r in 0 until rows) for (c in 0 until cols) {
            val value = board[r][c] ?: continue
            if (c + 2 < cols && board[r][c + 1] == value && board[r][c + 2] == value) (0..2).forEach { found.add(r to c + it) }
            if (r + 2 < rows && board[r + 1][c] == value && board[r + 2][c] == value) (0..2).forEach { found.add(r + it to c) }
        }
        return found
    }

    fun bestSwap(input: List<List<Int?>>): Pair<Pair<Int, Int>, Pair<Int, Int>>? {
        if (input.size < 3 || input.any { it.size != input[0].size } || input[0].size < 3) return null
        val board = input.map { it.toMutableList() }
        // Wait during visible matches/cascades; do not try to move an unstable board.
        if (matches(board).isNotEmpty()) return null
        var best: Pair<Pair<Int, Int>, Pair<Int, Int>>? = null
        var bestScore = 0
        for (r in board.indices) for (c in board[0].indices) {
            for ((dr, dc) in listOf(0 to 1, 1 to 0)) {
                val nr = r + dr; val nc = c + dc
                if (nr >= board.size || nc >= board[0].size) continue
                val a = board[r][c]; val b = board[nr][nc]
                if (a == null || b == null || a == b) continue
                board[r][c] = b; board[nr][nc] = a
                val matched = matches(board)
                board[r][c] = a; board[nr][nc] = b
                if ((r to c) !in matched && (nr to nc) !in matched) continue
                if (matched.size > bestScore) { best = (r to c) to (nr to nc); bestScore = matched.size }
            }
        }
        return best
    }

    fun analyze(bitmap: Bitmap, config: Calibration): Move? {
        require(config.valid() && bitmap.width == config.width && bitmap.height == config.height) { "L'écran a changé : recalibrez le bot." }
        val cells = board(bitmap, config)
        if (cells.sumOf { row -> row.count { it != null } } < config.rows * config.cols * .6f) return null
        val swap = bestSwap(cells) ?: return null
        fun center(cell: Pair<Int, Int>): Pair<Float, Float> =
            (config.left + (cell.second + .5f) * (config.right - config.left) / config.cols) to
                (config.top + (cell.first + .5f) * (config.bottom - config.top) / config.rows)
        val (x1, y1) = center(swap.first); val (x2, y2) = center(swap.second)
        return Move(x1, y1, x2, y2)
    }
}
