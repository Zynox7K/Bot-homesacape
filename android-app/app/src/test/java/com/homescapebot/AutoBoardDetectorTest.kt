package com.homescapebot

import org.junit.Assert.*
import org.junit.Test
import java.io.File

class AutoBoardDetectorTest {
    @Test fun userScreenshotFindsPiecesAndAnAdjacentMatch() {
        val root = generateSequence(File(System.getProperty("user.dir")).absoluteFile) { it.parentFile }
            .firstOrNull { File(it, "android-app/app/src/test/resources/user-board-480x300.rgb").isFile }
        assertNotNull("The test frame derived from the user screenshot must be present", root)
        val bytes = File(root!!, "android-app/app/src/test/resources/user-board-480x300.rgb").readBytes()
        val width = 480; val height = 300
        assertEquals(width * height * 3, bytes.size)
        val pixels = IntArray(width * height) { i ->
            ((bytes[i * 3].toInt() and 255) shl 16) or ((bytes[i * 3 + 1].toInt() and 255) shl 8) or (bytes[i * 3 + 2].toInt() and 255)
        }
        val board = AutoBoardDetector.detect(pixels, width, height)
        assertNotNull("The screenshot board should be detected without manual calibration", board)
        assertTrue("Recognize at least 15 tiles", board!!.cells.sumOf { row -> row.count { it != null } } >= 15)
        val move = board.move()
        assertNotNull("The screenshot contains a match-3 move", move)
        assertTrue(move!!.x1 in .20f.. .60f && move.y1 in .10f.. .75f)
        assertTrue(move.x2 in .20f.. .60f && move.y2 in .10f.. .75f)
    }

    @Test fun blankFrameAndTooFewColorsDoNotCauseGestures() {
        assertNull(AutoBoardDetector.detect(IntArray(480 * 300), 480, 300))
        assertNull(AutoBoardDetector.detect(IntArray(3), 480, 300))
    }
}
