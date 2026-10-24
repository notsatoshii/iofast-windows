package com.iofast.android

import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.iofast.android.diag.ProblemLog

/**
 * Fallback capture screen (the shortcut now goes to the service's
 * accessibility button callback); not a launcher entry.
 * For social apps, signals the accessibility service to run the share-flow
 * and exits immediately so the social app regains focus (required for
 * a11y clicks). For browsers, reads the URL bar from the last snapshot.
 * If the a11y service is off, opens the iofast home screen, which shows
 * the status and the button to turn it on.
 */
open class CaptureTrampolineActivity : AppCompatActivity() {

    /** Which shortcut opened this screen, for the problem log. */
    protected open val trigger = "side key"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        try {
            capture()
        } catch (t: Throwable) {
            ProblemLog.note("capture", "failed: $t")
            Toast.makeText(this, R.string.toast_capture_error, Toast.LENGTH_LONG).show()
        }
        finish()
    }

    private fun capture() {
        // Only a focused activity can read the clipboard; keep it so the
        // share flow can put it back after Copy link.
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val previousClip = try { cm.primaryClip?.getItemAt(0)?.text?.toString() } catch (_: Throwable) { null }
        CaptureRouter.capture(this, trigger, previousClip, ProblemLog.isServiceEnabled(this))
    }
}
