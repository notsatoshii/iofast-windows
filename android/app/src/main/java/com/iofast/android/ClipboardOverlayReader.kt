package com.iofast.android

import android.accessibilityservice.AccessibilityService
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.PixelFormat
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import com.iofast.android.diag.ProblemLog
import com.iofast.android.extractor.ClipboardCleaner
import com.iofast.android.send.CaptureNotifier
import com.iofast.android.send.Captures

/**
 * Reads the clipboard after Copy link without starting an activity.
 *
 * Android 10+ only lets the app owning the focused window read the
 * clipboard. A focusable 1x1 accessibility overlay gives the service that
 * focus, and unlike an activity it does not pause the app behind, so
 * YouTube never enters picture-in-picture. If focus never arrives the
 * caller falls back to [ClipboardReadActivity].
 */
object ClipboardOverlayReader {

    private const val FOCUS_WAIT_MS = 1_000L

    fun read(
        service: AccessibilityService,
        packageName: String,
        previousClip: String?,
        onNoFocus: () -> Unit
    ) {
        val wm = service.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val handler = Handler(Looper.getMainLooper())
        var done = false
        lateinit var view: View

        fun remove() {
            try { wm.removeView(view) } catch (t: Throwable) { ProblemLog.note("overlay", "remove failed: $t") }
        }

        val giveUp = Runnable {
            if (done) return@Runnable
            done = true
            remove()
            ProblemLog.note("overlay", "never got focus; falling back to activity")
            onNoFocus()
        }

        view = object : View(service) {
            override fun onWindowFocusChanged(hasWindowFocus: Boolean) {
                super.onWindowFocusChanged(hasWindowFocus)
                if (!hasWindowFocus || done) return
                done = true
                handler.removeCallbacks(giveUp)
                try {
                    readAndRecord(service, packageName, previousClip)
                } catch (t: Throwable) {
                    ProblemLog.note("overlay", "read failed: $t")
                    CaptureNotifier.couldNotRead(service)
                }
                remove()
            }
        }

        val params = WindowManager.LayoutParams(
            1, 1,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        ).apply { gravity = Gravity.TOP or Gravity.START }

        try {
            wm.addView(view, params)
        } catch (t: Throwable) {
            ProblemLog.note("overlay", "add failed: $t")
            done = true
            onNoFocus()
            return
        }
        handler.postDelayed(giveUp, FOCUS_WAIT_MS)
    }

    private fun readAndRecord(context: Context, packageName: String, previousClip: String?) {
        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clipText = cm.primaryClip?.getItemAt(0)?.text?.toString()
        val url = if (clipText != null && clipText.startsWith("http")) {
            ClipboardCleaner.cleanForPackage(clipText, packageName)
        } else {
            null
        }
        ProblemLog.note("overlay", "$packageName: ${url ?: "clipboard had no link"}")
        if (url != null) Captures.record(context, url, packageName) else CaptureNotifier.couldNotRead(context)
        if (previousClip != null) cm.setPrimaryClip(ClipData.newPlainText("restored", previousClip))
    }
}
