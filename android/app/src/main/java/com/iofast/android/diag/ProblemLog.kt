package com.iofast.android.diag

import android.accessibilityservice.AccessibilityServiceInfo
import android.app.ActivityManager
import android.app.ApplicationExitInfo
import android.content.Context
import android.os.Build
import android.view.accessibility.AccessibilityManager
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * On-phone diagnostics with no adb: crashes, recent breadcrumbs and the
 * system's own record of why the process died (ANR, crash, low memory).
 * The home screen shows [report] with Copy / Share buttons.
 */
object ProblemLog {
    private const val CRASH_FILE = "crash.log"
    private const val TRAIL_FILE = "breadcrumbs.log"
    private const val MAX_FILE_BYTES = 64 * 1024
    private const val KEEP_TRAIL_LINES = 300
    private const val REPORT_TRAIL_LINES = 120

    private var dir: File? = null
    private val stamp = SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US)

    fun init(context: Context) {
        dir = context.filesDir
    }

    /** Appends one breadcrumb line. Never throws. */
    @Synchronized
    fun note(tag: String, message: String) {
        val d = dir ?: return
        try {
            val f = File(d, TRAIL_FILE)
            f.appendText("${stamp.format(Date())} [$tag] $message\n")
            if (f.length() > MAX_FILE_BYTES) {
                f.writeText(f.readLines().takeLast(KEEP_TRAIL_LINES).joinToString("\n", postfix = "\n"))
            }
        } catch (_: Throwable) {
        }
    }

    /** Called from the uncaught-exception handler before the process dies. */
    @Synchronized
    fun recordCrash(thread: Thread, error: Throwable) {
        val d = dir ?: return
        try {
            val sw = StringWriter()
            error.printStackTrace(PrintWriter(sw))
            val f = File(d, CRASH_FILE)
            f.appendText("=== crash ${stamp.format(Date())} on thread ${thread.name}\n$sw\n")
            if (f.length() > MAX_FILE_BYTES) {
                val text = f.readText()
                f.writeText(text.takeLast(MAX_FILE_BYTES / 2))
            }
        } catch (_: Throwable) {
        }
    }

    @Synchronized
    fun clear() {
        val d = dir ?: return
        File(d, CRASH_FILE).delete()
        File(d, TRAIL_FILE).delete()
    }

    fun report(context: Context): String = buildString {
        val pkg = context.packageName
        val version = try {
            context.packageManager.getPackageInfo(pkg, 0).versionName
        } catch (_: Throwable) {
            "?"
        }
        appendLine("iofast $version · ${Build.MANUFACTURER} ${Build.MODEL} · Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})")
        appendLine("accessibility on: ${isServiceEnabled(context)}")
        appendLine()

        appendLine("== Why the app last stopped (system record)")
        val exits = exitReasons(context)
        if (exits.isEmpty()) appendLine("(none recorded)") else exits.forEach { appendLine(it) }
        appendLine()

        appendLine("== Crashes")
        val crash = dir?.let { File(it, CRASH_FILE) }
        if (crash == null || !crash.exists()) appendLine("(none)") else append(crash.readText().takeLast(12_000))
        appendLine()

        appendLine("== Recent steps")
        val trail = dir?.let { File(it, TRAIL_FILE) }
        if (trail == null || !trail.exists()) appendLine("(none)")
        else trail.readLines().takeLast(REPORT_TRAIL_LINES).forEach { appendLine(it) }
    }

    fun isServiceEnabled(context: Context): Boolean = try {
        val am = context.getSystemService(Context.ACCESSIBILITY_SERVICE) as AccessibilityManager
        am.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK).any { info ->
            info.resolveInfo?.serviceInfo?.let {
                it.packageName == context.packageName && it.name.endsWith(".UrlCaptureAccessibilityService")
            } == true
        }
    } catch (_: Throwable) {
        false
    }

    private fun exitReasons(context: Context): List<String> = try {
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        am.getHistoricalProcessExitReasons(context.packageName, 0, 8).map { e ->
            val desc = e.description?.let { " · $it" } ?: ""
            "${stamp.format(Date(e.timestamp))} ${reasonName(e.reason)}$desc"
        }
    } catch (t: Throwable) {
        listOf("(could not read: $t)")
    }

    private fun reasonName(reason: Int): String = when (reason) {
        ApplicationExitInfo.REASON_ANR -> "ANR (app froze)"
        ApplicationExitInfo.REASON_CRASH -> "CRASH"
        ApplicationExitInfo.REASON_CRASH_NATIVE -> "NATIVE CRASH"
        ApplicationExitInfo.REASON_LOW_MEMORY -> "LOW MEMORY"
        ApplicationExitInfo.REASON_EXCESSIVE_RESOURCE_USAGE -> "EXCESSIVE RESOURCE USE"
        ApplicationExitInfo.REASON_USER_REQUESTED -> "USER REQUESTED (force stop / update)"
        ApplicationExitInfo.REASON_USER_STOPPED -> "USER STOPPED"
        ApplicationExitInfo.REASON_PERMISSION_CHANGE -> "PERMISSION CHANGE"
        ApplicationExitInfo.REASON_SIGNALED -> "KILLED BY SIGNAL"
        ApplicationExitInfo.REASON_EXIT_SELF -> "EXITED"
        ApplicationExitInfo.REASON_INITIALIZATION_FAILURE -> "STARTUP FAILURE"
        ApplicationExitInfo.REASON_DEPENDENCY_DIED -> "DEPENDENCY DIED"
        ApplicationExitInfo.REASON_OTHER -> "OTHER"
        else -> "UNKNOWN ($reason)"
    }
}
