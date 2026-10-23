package com.iofast.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.iofast.android.data.AppDatabase
import com.iofast.android.data.CapturedLink
import com.iofast.android.diag.ProblemLog
import com.iofast.android.extractor.ClipboardCleaner
import kotlinx.coroutines.launch

/**
 * Transparent activity launched after the share-flow clicks Copy-link.
 * Android 10+ only lets the focused app read the clipboard, and focus
 * arrives after onCreate, so the read happens in onWindowFocusChanged.
 * Saves the cleaned URL, toasts, restores the previous clipboard, finishes.
 */
class ClipboardReadActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_PACKAGE = "extra_package"
        const val EXTRA_PREVIOUS_CLIP = "extra_previous_clip"
        private const val FOCUS_WAIT_MS = 1_500L
    }

    private var done = false
    private val handler = Handler(Looper.getMainLooper())
    private val giveUp = Runnable {
        if (!done) {
            done = true
            ProblemLog.note("clipboard", "never got focus; clipboard not read")
            Toast.makeText(this, R.string.toast_fallback_share, Toast.LENGTH_LONG).show()
            finish()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handler.postDelayed(giveUp, FOCUS_WAIT_MS)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus || done) return
        done = true
        handler.removeCallbacks(giveUp)
        try {
            readClipboard()
        } catch (t: Throwable) {
            ProblemLog.note("clipboard", "failed: $t")
        }
        finish()
    }

    private fun readClipboard() {
        val packageName = intent.getStringExtra(EXTRA_PACKAGE) ?: ""
        val previousClip = intent.getStringExtra(EXTRA_PREVIOUS_CLIP)

        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clipText = cm.primaryClip?.getItemAt(0)?.text?.toString()

        val url = if (clipText != null && clipText.startsWith("http")) {
            ClipboardCleaner.cleanForPackage(clipText, packageName)
        } else {
            null
        }
        ProblemLog.note("clipboard", "$packageName: ${url ?: "clipboard had no link"}")

        if (url != null) {
            val app = applicationContext
            IofastApp.appScope.launch {
                AppDatabase.get(app).capturedLinkDao()
                    .insert(CapturedLink(url = url, sourcePackage = packageName))
            }
            Toast.makeText(this, getString(R.string.toast_captured, url), Toast.LENGTH_LONG).show()
        } else {
            Toast.makeText(this, R.string.toast_fallback_share, Toast.LENGTH_LONG).show()
        }

        // Restore previous clipboard content
        if (previousClip != null) {
            cm.setPrimaryClip(ClipData.newPlainText("restored", previousClip))
        }
    }
}
