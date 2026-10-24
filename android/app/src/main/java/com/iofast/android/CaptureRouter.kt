package com.iofast.android

import android.content.Context
import android.content.Intent
import android.widget.Toast
import com.iofast.android.diag.ProblemLog
import com.iofast.android.send.CaptureNotifier
import com.iofast.android.send.Captures

/**
 * One capture, whoever triggered it: the accessibility button callback in
 * the service (no window opens, so the app in front never leaves) or the
 * shortcut activity (fallback; its launch makes YouTube go picture-in-picture).
 */
object CaptureRouter {

    enum class Route { NO_SNAPSHOT, SHARE_FLOW, BROWSER, UNSUPPORTED }

    private val SHARE_FLOW_PACKAGES = setOf(
        "com.google.android.youtube",
        "com.twitter.android", "com.x.android",
        "com.instagram.android"
    )

    private val BROWSER_PACKAGES = setOf(
        "com.android.chrome",
        "com.sec.android.app.sbrowser"
    )

    fun routeFor(packageName: String?): Route = when (packageName) {
        null -> Route.NO_SNAPSHOT
        in SHARE_FLOW_PACKAGES -> Route.SHARE_FLOW
        in BROWSER_PACKAGES -> Route.BROWSER
        else -> Route.UNSUPPORTED
    }

    /**
     * @param previousClip clipboard text to put back after Copy link; only a
     *   focused activity can read it, so the service path passes null.
     * @param serviceRunning false sends the user to the home screen to turn it on.
     */
    fun capture(context: Context, trigger: String, previousClip: String?, serviceRunning: Boolean) {
        ProblemLog.note("capture", "triggered by $trigger")
        if (!serviceRunning) {
            ProblemLog.note("capture", "pressed but accessibility is off")
            Toast.makeText(context, R.string.a11y_disabled_toast, Toast.LENGTH_LONG).show()
            context.startActivity(Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }

        val snap = UrlCaptureAccessibilityService.getBestSnapshot()
        when (routeFor(snap?.packageName)) {
            Route.NO_SNAPSHOT -> {
                ProblemLog.note("capture", "no snapshot: no browser or supported app seen in front")
                Toast.makeText(context, R.string.toast_no_url, Toast.LENGTH_SHORT).show()
            }
            Route.SHARE_FLOW -> {
                val pkg = snap!!.packageName
                ProblemLog.note("capture", "asking $pkg for its link")
                UrlCaptureAccessibilityService.startShareFlow(pkg, previousClip)
            }
            Route.BROWSER -> {
                val pkg = snap!!.packageName
                val url = UrlCaptureAccessibilityService.extractLastUrl()
                ProblemLog.note("capture", "$pkg: ${url ?: "no url in address bar"}")
                if (url != null) Captures.record(context, url, pkg) else CaptureNotifier.couldNotRead(context)
            }
            Route.UNSUPPORTED -> CaptureNotifier.couldNotRead(context)
        }
    }
}
