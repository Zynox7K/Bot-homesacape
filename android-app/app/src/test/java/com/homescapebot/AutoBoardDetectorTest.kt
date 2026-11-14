package com.homescapebot

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import javax.imageio.ImageIO

class AutoBoardDetectorTest {
    @Test fun userScreenshotFindsPiecesAndAnAdjacentMatch() {
        val root = generateSequence(File(System.getProperty("user.dir")).absoluteFile) { it.parentFile }
            .firstOrNull { File(it, "Screenshot_20261114-092352~2.jpg").isFile }
        assertNotNull("The user-provided screenshot must be present in the repository", root)
        val image = ImageIO.read(File(root!!, "Screenshot_20261114-092352~2.jpg"))
        val step = maxOf(image.width, image.height) / 480
        val width = image.width / step; val height = image.height / step
        val pixels = IntArray(width * height) { i -> image.getRGB((i % width) * step, (i / width) * step) }
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
