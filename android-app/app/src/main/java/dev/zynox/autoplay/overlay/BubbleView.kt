package dev.zynox.autoplay.overlay

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.os.Handler
import android.os.Looper
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import dev.zynox.autoplay.BotState
import dev.zynox.autoplay.R
import kotlin.math.hypot

/**
 * Bulle flottante du pilote.
 *
 * Une fenêtre superposée ouverte depuis un `Service` n'a pas de propriétaire de
 * composition : un `ComposeView` y exigerait de fournir `LifecycleOwner`,
 * `ViewModelStoreOwner` et `SavedStateRegistryOwner`. Cette vue dessinée à la
 * main évite cette plomberie et reste sans allocation pendant le glissement,
 * ce qui compte pour un élément affiché en permanence par-dessus le jeu.
 *
 * Elle distingue trois interactions : appui court (lecture/pause), appui long
 * (retour aux réglages) et glissement (déplacement de la bulle).
 */
class BubbleView(context: Context, private val callbacks: Callbacks) : View(context) {

    interface Callbacks {
        fun onTap()
        fun onLongPress()
        fun onDrag(deltaX: Int, deltaY: Int)
        fun onDragEnd()
    }

    private val density = resources.displayMetrics.density
    private val sizePx = (SIZE_DP * density).toInt()
    private val touchSlop = ViewConfiguration.get(context).scaledTouchSlop.toFloat()

    private val backgroundPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.argb(200, 13, 17, 23)
    }
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = RING_WIDTH_DP * density
    }
    private val glyphPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.WHITE
    }

    private val trianglePath = Path()
    private val barRect = RectF()
    private val handler = Handler(Looper.getMainLooper())
    private val longPressRunnable = Runnable {
        if (!dragging) {
            performHapticFeedback(HapticFeedbackConstants.LONG_PRESS)
            callbacks.onLongPress()
        }
    }

    private var downRawX = 0f
    private var downRawY = 0f
    private var lastRawX = 0f
    private var lastRawY = 0f
    private var downAt = 0L
    private var dragging = false

    private val colorIdle = context.getColor(R.color.text_secondary)
    private val colorActive = context.getColor(R.color.brand)
    private val colorAccent = context.getColor(R.color.accent)
    private val colorWarning = context.getColor(R.color.warning)
    private val colorDanger = context.getColor(R.color.danger)

    /** État affiché par l'anneau coloré. */
    var state: BotState = BotState.IDLE
        set(value) {
            if (field == value) return
            field = value
            ringPaint.color = colorFor(value)
            invalidate()
        }

    /** Pictogramme affiché : pause si le pilote joue, lecture sinon. */
    var playing: Boolean = false
        set(value) {
            if (field == value) return
            field = value
            invalidate()
        }

    init {
        ringPaint.color = colorFor(state)
        isClickable = true
        isFocusable = false
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        setMeasuredDimension(sizePx, sizePx)
    }

    override fun onDraw(canvas: Canvas) {
        val center = sizePx / 2f
        val ringRadius = center - ringPaint.strokeWidth
        canvas.drawCircle(center, center, ringRadius, backgroundPaint)
        canvas.drawCircle(center, center, ringRadius - ringPaint.strokeWidth / 2f, ringPaint)

        val half = sizePx * GLYPH_RATIO
        if (playing) {
            val barWidth = half * 0.6f
            barRect.set(center - half, center - half, center - half + barWidth, center + half)
            canvas.drawRoundRect(barRect, barWidth / 2f, barWidth / 2f, glyphPaint)
            barRect.set(center + half - barWidth, center - half, center + half, center + half)
            canvas.drawRoundRect(barRect, barWidth / 2f, barWidth / 2f, glyphPaint)
        } else {
            trianglePath.reset()
            trianglePath.moveTo(center - half * 0.8f, center - half)
            trianglePath.lineTo(center + half, center)
            trianglePath.lineTo(center - half * 0.8f, center + half)
            trianglePath.close()
            canvas.drawPath(trianglePath, glyphPaint)
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downRawX = event.rawX
                downRawY = event.rawY
                lastRawX = event.rawX
                lastRawY = event.rawY
                downAt = System.currentTimeMillis()
                dragging = false
                handler.postDelayed(longPressRunnable, LONG_PRESS_MS)
                parent?.requestDisallowInterceptTouchEvent(true)
                return true
            }

            MotionEvent.ACTION_MOVE -> {
                val deltaX = event.rawX - downRawX
                val deltaY = event.rawY - downRawY
                if (!dragging && hypot(deltaX, deltaY) > touchSlop) {
                    dragging = true
                    handler.removeCallbacks(longPressRunnable)
                }
                if (dragging) {
                    val stepX = (event.rawX - lastRawX).toInt()
                    val stepY = (event.rawY - lastRawY).toInt()
                    lastRawX = event.rawX
                    lastRawY = event.rawY
                    if (stepX != 0 || stepY != 0) callbacks.onDrag(stepX, stepY)
                }
                return true
            }

            MotionEvent.ACTION_UP -> {
                handler.removeCallbacks(longPressRunnable)
                val wasDragging = dragging
                dragging = false
                if (wasDragging) {
                    callbacks.onDragEnd()
                } else if (System.currentTimeMillis() - downAt < TAP_MS) {
                    performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
                    callbacks.onTap()
                }
                return true
            }

            MotionEvent.ACTION_CANCEL -> {
                handler.removeCallbacks(longPressRunnable)
                val wasDragging = dragging
                dragging = false
                if (wasDragging) callbacks.onDragEnd()
                return true
            }
        }
        return super.onTouchEvent(event)
    }

    private fun colorFor(value: BotState): Int = when (value) {
        BotState.PLAYING, BotState.CAPTURING, BotState.THINKING, BotState.WAITING -> colorActive
        BotState.DRY_RUN -> colorAccent
        BotState.PAUSED -> colorWarning
        BotState.ERROR -> colorDanger
        BotState.IDLE, BotState.STOPPED -> colorIdle
    }

    companion object {
        private const val SIZE_DP = 56f
        private const val RING_WIDTH_DP = 3f
        private const val GLYPH_RATIO = 0.16f
        private const val LONG_PRESS_MS = 550L
        private const val TAP_MS = 300L

        /** Diamètre de la bulle en pixels, pour le placement initial. */
        fun sizePx(context: Context): Int = (SIZE_DP * context.resources.displayMetrics.density).toInt()
    }
}
