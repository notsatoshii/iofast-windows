package com.iofast.android

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.iofast.android.extractor.*
import com.iofast.android.shareflow.AppShareLabels
import com.iofast.android.shareflow.NodeClicker
import com.iofast.android.shareflow.ShareFlowStateMachine

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
        private val snapshotMap = mutableMapOf<String, Snapshot>()

        @Volatile var lastRelevantSnapshot: Snapshot? = null
            private set

        @Volatile var shareFlowActive: Boolean = false
            private set

        @Volatile var shareFlowPackage: String? = null
            private set

        @Volatile var shareFlowPreviousClipboard: String? = null
            private set

        /** The running state machine, if any. Accessed only from main thread. */
        @Volatile var activeStateMachine: ShareFlowStateMachine? = null
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

        private const val STALENESS_MS = 10_000L

        private val extractors: List<UrlExtractor> = listOf(
            ChromeExtractor(),
            SamsungInternetExtractor(),
            YouTubeExtractor(),
            XExtractor(),
            InstagramExtractor()
        )

        fun getBestSnapshot(): Snapshot? {
            val now = System.currentTimeMillis()
            snapshotMap.entries.removeIf { now - it.value.timestamp > STALENESS_MS }
            return lastRelevantSnapshot?.takeIf { now - it.timestamp <= STALENESS_MS }
        }

        fun extractLastUrl(): String? {
            val snap = getBestSnapshot() ?: return null
            val extractor = extractors.firstOrNull { snap.packageName in it.packages }
                ?: return null
            return extractor.extract(snap.viewTreeText)
        }

        fun startShareFlow(packageName: String, previousClipboard: String?) {
            shareFlowActive = true
            shareFlowPackage = packageName
            shareFlowPreviousClipboard = previousClipboard
        }

        fun endShareFlow() {
            shareFlowActive = false
            shareFlowPackage = null
            shareFlowPreviousClipboard = null
            activeStateMachine = null
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        val pkg = event.packageName?.toString() ?: return

        // If a share flow is active, feed events to the state machine
        if (shareFlowActive && pkg == shareFlowPackage) {
            val root = rootInActiveWindow
            activeStateMachine?.onWindowUpdate(root)
            return
        }

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

                // If we were waiting for a share flow to start and this is the target app
                // regaining focus after the trampoline exited, kick off the state machine
                if (shareFlowActive && pkg == shareFlowPackage && activeStateMachine == null) {
                    launchShareFlowStateMachine(pkg)
                }
            }
        }
    }

    override fun onInterrupt() {}

    private fun launchShareFlowStateMachine(packageName: String) {
        val labels = AppShareLabels.forPackage(packageName) ?: run {
            endShareFlow()
            return
        }

        val clicker = NodeClicker { node ->
            node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        }

        val sm = ShareFlowStateMachine(
            labels = labels,
            clicker = clicker,
            backAction = { performGlobalAction(GLOBAL_ACTION_BACK) },
            onSuccess = {
                // Launch ClipboardReadActivity which can read clipboard (it's focused)
                val intent = Intent(this, ClipboardReadActivity::class.java).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_ANIMATION)
                    putExtra(ClipboardReadActivity.EXTRA_PACKAGE, packageName)
                    putExtra(ClipboardReadActivity.EXTRA_PREVIOUS_CLIP, shareFlowPreviousClipboard)
                }
                startActivity(intent)
                endShareFlow()
            },
            onFail = {
                endShareFlow()
            }
        )

        activeStateMachine = sm
        sm.start()

        // Feed the current root immediately in case the window is already stable
        val root = rootInActiveWindow
        if (root != null) {
            sm.onWindowUpdate(root)
        }
    }

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