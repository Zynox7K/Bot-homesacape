package com.homescapebot

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/** Conservative, lightweight color-component and lattice detector; all coordinates refer to the sampled frame. */
object AutoBoardDetector {
    data class Board(val cells: List<List<Int?>>, val leftX: Float, val topY: Float, val pitch: Float, val width: Int, val height: Int) {
        fun move(): Move? {
            val (a, b) = LocalEngine.bestSwap(cells) ?: return null
            return Move((leftX + a.second * pitch) / width, (topY + a.first * pitch) / height,
                (leftX + b.second * pitch) / width, (topY + b.first * pitch) / height)
        }
        fun samePosition(other: Board): Boolean = width == other.width && height == other.height && cells == other.cells &&
            abs(leftX - other.leftX) < pitch * .20f && abs(topY - other.topY) < pitch * .20f && abs(pitch - other.pitch) < pitch * .10f
    }
    private data class Component(val x: Float, val y: Float, val color: Int)

    /** Input is a small RGB frame, preferably no larger than 480 pixels on the longest side. */
    fun detect(pixels: IntArray, width: Int, height: Int): Board? {
        if (width < 100 || height < 100 || pixels.size != width * height) return null
        val types = ByteArray(pixels.size)
        val left = (width * .20f).toInt(); val right = (width * .80f).toInt()
        val top = (height * .10f).toInt(); val bottom = (height * .90f).toInt()
        for (y in top until bottom) for (x in left until right) {
            val rgb = pixels[y * width + x]
            val r = rgb shr 16 and 255; val g = rgb shr 8 and 255; val b = rgb and 255
            types[y * width + x] = when {
                r > 160 && r > g * 1.8f && r > b * 1.8f && g < 110 && b < 100 -> 1
                g > 110 && g > r * 1.6f && g > b * 1.6f && r < 105 && b < 105 -> 2
                b > 160 && b > r * 1.8f && b > g * 1.5f && r < 100 -> 3
                r > 160 && g > 140 && b < 105 && abs(r - g) < 100 -> 4
                else -> 0
            }.toByte()
        }
        val queue = IntArray(pixels.size)
        val pieces = ArrayList<Component>()
        val minArea = height * height * .0019f
        val minSize = height * .03f; val maxSize = height * .12f
        for (y in top until bottom) for (x in left until right) {
            val start = y * width + x
            val type = types[start]
            if (type.toInt() == 0) continue
            var head = 0; var tail = 0
            queue[tail++] = start; types[start] = 0
            var sumX = 0L; var sumY = 0L
            var x0 = x; var x1 = x; var y0 = y; var y1 = y
            while (head < tail) {
                val index = queue[head++]
                val px = index % width; val py = index / width
                sumX += px; sumY += py
                x0 = min(x0, px); x1 = max(x1, px); y0 = min(y0, py); y1 = max(y1, py)
                fun add(nx: Int, ny: Int) {
                    if (nx < left || nx >= right || ny < top || ny >= bottom) return
                    val ni = ny * width + nx
                    if (types[ni] == type) { types[ni] = 0; queue[tail++] = ni }
                }
                add(px - 1, py); add(px + 1, py); add(px, py - 1); add(px, py + 1)
            }
            val w = x1 - x0 + 1; val h = y1 - y0 + 1
            if (tail >= minArea && w >= minSize && h >= minSize && w <= maxSize && h <= maxSize && w.toFloat() / h in .45f..2.0f) {
                pieces.add(Component(sumX.toFloat() / tail, sumY.toFloat() / tail, type.toInt() - 1))
            }
        }
        if (pieces.size < 10 || pieces.size > 200 || pieces.map { it.color }.distinct().size < 3) return null
        val distances = ArrayList<Float>()
        for (i in pieces.indices) for (j in 0 until i) {
            val dx = abs(pieces[i].x - pieces[j].x); val dy = abs(pieces[i].y - pieces[j].y)
            val minPitch = height * .045f; val maxPitch = height * .12f
            if (dy < height * .02f && dx in minPitch..maxPitch) distances.add(dx)
            if (dx < height * .02f && dy in minPitch..maxPitch) distances.add(dy)
        }
        if (distances.size < 4) return null
        val best = distances.maxByOrNull { d -> distances.count { abs(it - d) <= d * .08f } } ?: return null
        val nearby = distances.filter { abs(it - best) <= best * .08f }.sorted()
        if (nearby.size < 4) return null
        val pitch = nearby[nearby.size / 2]
        var anchor: Component? = null
        var bestCount = 0; var bestError = Float.MAX_VALUE
        for (candidate in pieces) {
            var count = 0; var error = 0f
            for (p in pieces) {
                val dx = (p.x - candidate.x) / pitch; val dy = (p.y - candidate.y) / pitch
                val ex = abs(dx - dx.roundToInt()); val ey = abs(dy - dy.roundToInt())
                if (ex < .20f && ey < .20f) { count++; error += ex + ey }
            }
            if (count > bestCount || (count == bestCount && error < bestError)) { anchor = candidate; bestCount = count; bestError = error }
        }
        val a = anchor ?: return null
        if (bestCount < 10 || bestCount < pieces.size * .70f) return null
        val included = pieces.filter { p ->
            val dx = (p.x - a.x) / pitch; val dy = (p.y - a.y) / pitch
            abs(dx - dx.roundToInt()) < .20f && abs(dy - dy.roundToInt()) < .20f
        }
        val indexed = included.map { Triple(((it.y - a.y) / pitch).roundToInt(), ((it.x - a.x) / pitch).roundToInt(), it.color) }
        val minR = indexed.minOf { it.first }; val maxR = indexed.maxOf { it.first }
        val minC = indexed.minOf { it.second }; val maxC = indexed.maxOf { it.second }
        val rows = maxR - minR + 1; val cols = maxC - minC + 1
        if (rows !in 3..15 || cols !in 3..15) return null
        val board = List(rows) { MutableList<Int?>(cols) { null } }
        val occupied = mutableSetOf<Pair<Int, Int>>()
        for ((r, c, color) in indexed) {
            val cell = (r - minR) to (c - minC)
            // Conflicting colored components in a single cell indicate an uncertain screen.
            if (!occupied.add(cell)) return null
            board[cell.first][cell.second] = color
        }
        return Board(board, a.x + minC * pitch, a.y + minR * pitch, pitch, width, height)
    }
}
