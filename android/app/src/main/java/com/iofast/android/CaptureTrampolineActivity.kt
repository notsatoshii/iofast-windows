package com.iofast.android

import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.iofast.android.diag.ProblemLog
import com.iofast.android.send.CaptureNotifier
import com.iofast.android.send.Captures

/**
 * The "iofast capture" launcher entry; the side-key double press opens it.
 * For social apps, signals the accessibility service to run the share-flow
 * and exits immediately so the social app regains focus (required for
 * a11y clicks). For browsers, reads the URL bar from the last snapshot.
 * If the a11y service is off, opens the iofast home screen, which shows
 * the status and the button to turn it on.
 */
class CaptureTrampolineActivity : AppCompatActivity() {

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
        if (!ProblemLog.isServiceEnabled(this)) {
            ProblemLog.note("capture", "side key pressed but accessibility is off")
            Toast.makeText(this, R.string.a11y_disabled_toast, Toast.LENGTH_LONG).show()
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }

        val snap = UrlCaptureAccessibilityService.getBestSnapshot()
        if (snap == null) {
            ProblemLog.note("capture", "no snapshot: no browser or supported app seen in front")
            Toast.makeText(this, R.string.toast_no_url, Toast.LENGTH_SHORT).show()
            return
        }

        when (snap.packageName) {
            "com.google.android.youtube",
            "com.twitter.android", "com.x.android",
            "com.instagram.android" -> {
                // Save previous clipboard for restoration
                val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                val previousClip = cm.primaryClip?.getItemAt(0)?.text?.toString()

                // Signal the service; finish() in onCreate lets the social
                // app regain focus so the a11y service can click inside it
                ProblemLog.note("capture", "asking ${snap.packageName} for its link")
                UrlCaptureAccessibilityService.startShareFlow(
                    packageName = snap.packageName,
                    previousClipboard = previousClip
                )
            }

            "com.android.chrome",
            "com.sec.android.app.sbrowser" -> {
                val url = UrlCaptureAccessibilityService.extractLastUrl()
                ProblemLog.note("capture", "${snap.packageName}: ${url ?: "no url in address bar"}")
                if (url != null) {
                    Captures.record(this, url, snap.packageName)
                } else {
                    CaptureNotifier.couldNotRead(this)
                }
            }

            else -> CaptureNotifier.couldNotRead(this)
        }
    }
}
