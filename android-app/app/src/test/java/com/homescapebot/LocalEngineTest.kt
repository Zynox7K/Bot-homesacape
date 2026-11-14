package com.homescapebot

import org.junit.Assert.*
import org.junit.Test

class LocalEngineTest {
    @Test fun findsNewMatchWithoutMutatingInput() {
        val board = listOf(listOf(0, 2, 0), listOf(1, 0, 1), listOf(2, 1, 2))
        assertEquals((0 to 1) to (1 to 1), LocalEngine.bestSwap(board))
        assertEquals(listOf(0, 2, 0), board[0])
    }

    @Test fun avoidsUnknownAndExistingMatches() {
        assertNull(LocalEngine.bestSwap(List(3) { List<Int?>(3) { null } }))
        val board = listOf(listOf(0, 0, 0), listOf(1, 2, 1), listOf(2, 1, 2))
        assertEquals(setOf(0 to 0, 0 to 1, 0 to 2), LocalEngine.matches(board))
        assertNull(LocalEngine.bestSwap(board))
    }

    @Test fun invalidBoardsAreRejected() {
        assertNull(LocalEngine.bestSwap(emptyList()))
        assertNull(LocalEngine.bestSwap(listOf(listOf(0, 1, 2), listOf(1, 0))))
    }
}
