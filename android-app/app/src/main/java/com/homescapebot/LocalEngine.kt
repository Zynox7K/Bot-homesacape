package com.homescapebot

data class Move(val x1: Float, val y1: Float, val x2: Float, val y2: Float)

/** Conservative match-three solver. Unknown cells are never moved. */
object LocalEngine {
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

}
