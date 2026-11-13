package com.homescapebot

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Bitmap
import android.graphics.ColorSpace
import android.graphics.Path
import android.graphics.Point
import android.hardware.display.DisplayManager
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

class TouchAccessibilityService : AccessibilityService() {
    companion object {
        @Volatile var instance: TouchAccessibilityService? = null
            private set
        const val GAME_PACKAGE = "com.playrix.homescapes"
    }
    override fun onServiceConnected() { instance = this }
    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit
    override fun onInterrupt() = Unit
    override fun onDestroy() { if (instance === this) instance = null; super.onDestroy() }

    fun gameIsActive(): Boolean = rootInActiveWindow?.packageName?.toString() == GAME_PACKAGE

    suspend fun capture(): Bitmap? = suspendCancellableCoroutine { continuation ->
        if (!gameIsActive()) { continuation.resume(null); return@suspendCancellableCoroutine }
        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) {
                val buffer = result.hardwareBuffer
                val bitmap = try {
                    val hardware = Bitmap.wrapHardwareBuffer(buffer, result.colorSpace ?: ColorSpace.get(ColorSpace.Named.SRGB))
                    try { hardware?.copy(Bitmap.Config.ARGB_8888, false) } finally { hardware?.recycle() }
                } catch (_: Exception) { null } finally { buffer.close() }
                if (continuation.isActive) continuation.resume(bitmap) else bitmap?.recycle()
            }
            override fun onFailure(errorCode: Int) { if (continuation.isActive) continuation.resume(null) }
        })
    }

    suspend fun swipe(move: Move, width: Int, height: Int): Boolean = suspendCancellableCoroutine { continuation ->
        if (!gameIsActive()) { continuation.resume(false); return@suspendCancellableCoroutine }
        val size = Point()
        @Suppress("DEPRECATION")
        getSystemService(DisplayManager::class.java).getDisplay(Display.DEFAULT_DISPLAY)?.getRealSize(size)
        if (size.x != width || size.y != height) { continuation.resume(false); return@suspendCancellableCoroutine }
        val path = Path().apply { moveTo(move.x1 * width, move.y1 * height); lineTo(move.x2 * width, move.y2 * height) }
        val gesture = GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, 180)).build()
        val accepted = dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) { if (continuation.isActive) continuation.resume(true) }
            override fun onCancelled(gestureDescription: GestureDescription?) { if (continuation.isActive) continuation.resume(false) }
        }, null)
        if (!accepted && continuation.isActive) continuation.resume(false)
    }
}
