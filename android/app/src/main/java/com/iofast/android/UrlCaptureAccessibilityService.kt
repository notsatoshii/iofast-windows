package com.iofast.android

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.iofast.android.extractor.*

/**
 * Tracks foreground app snapshots and drives the Share->Copy-link flow
 * for social apps when the side key fires.
 *
 * Snapshot selection ignores our own package, SystemUI, launchers and
 * keyboards so the trampoline always reads the app that was in front
 * just before the side-key press.
 */
class UrlCaptureAccessibilityService : AccessibilityService() {

    data class Snapshot(
        val packageName: String,
        val viewTreeText: String,
        val timestamp: Long = System.currentTimeMillis()
    )

    companion object {
        /** Per-package snapshots; only relevant packages are stored. */
        private val snapshotMap = mutableMapOf<String, Snapshot>()

        @Volatile var lastRelevantSnapshot: Snapshot? = null
            private set

        /** True while the share-flow automation is running. */
        @Volatile var shareFlowActive: Boolean = false
            private set

        /** Package that triggered the current share flow, if any. */
        @Volatile var shareFlowPackage: String? = null
            private set

        private val IGNORED_PACKAGES = setOf(
            "com.iofast.android",
            "com.android.systemui",
            "com.android.launcher3",
            "com.sec.android.app.launcher",
            "com.google.android.inputmethod.latin",
            "com.samsung.android.honeyboard",
            "com.touchtype.swiftkey"
        )

        private val RELEVANT_PACKAGES = setOf(
            "com.android.chrome",
            "com.sec.android.app.sbrowser",
            "com.google.android.youtube",
            "com.twitter.android",
            "com.x.android",
            "com.instagram.android"
        )

        private val STALENESS_MS = 10_000L

        private val extractors: List<UrlExtractor> = listOf(
            ChromeExtractor(),
            SamsungInternetExtractor(),
            YouTubeExtractor(),
            XExtractor(),
            InstagramExtractor()
        )

        fun getBestSnapshot(): Snapshot? {
            val now = System.currentTimeMillis()
            // Clean stale entries
            snapshotMap.entries.removeIf { now - it.value.timestamp > STALENESS_MS }
            return lastRelevantSnapshot?.takeIf { now - it.timestamp <= STALENESS_MS }
        }

        fun extractLastUrl(): String? {
            val snap = getBestSnapshot() ?: return null
            val extractor = extractors.firstOrNull { snap.packageName in it.packages }
                ?: return null
            return extractor.extract(snap.viewTreeText)
        }

        fun startShareFlow(packageName: String) {
            shareFlowActive = true
            shareFlowPackage = packageName
        }

        fun endShareFlow() {
            shareFlowActive = false
            shareFlowPackage = null
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        val pkg = event.packageName?.toString() ?: return

        // Ignore irrelevant packages entirely
        if (pkg in IGNORED_PACKAGES) return
        if (pkg !in RELEVANT_PACKAGES) return

        when (event.eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> {
                val root = rootInActiveWindow ?: return
                val text = collectText(root)
                val snap = Snapshot(pkg, text)
                snapshotMap[pkg] = snap
                lastRelevantSnapshot = snap
            }
        }
    }

    override fun onInterrupt() {}

    private fun collectText(node: AccessibilityNodeInfo): String {
        val sb = StringBuilder()
        fun walk(n: AccessibilityNodeInfo) {
            n.text?.let { sb.append(it).append(' ') }
            n.contentDescription?.let { sb.append(it).append(' ') }
            n.viewIdResourceName?.let { sb.append("[$it] ") }
            for (i in 0 until n.childCount) {
                n.getChild(i)?.let { walk(it) }
            }
        }
        walk(node)
        return sb.toString()
    }
}