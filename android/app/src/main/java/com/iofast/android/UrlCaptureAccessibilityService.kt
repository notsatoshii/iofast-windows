package com.iofast.android

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.iofast.android.diag.ProblemLog
import com.iofast.android.send.CaptureNotifier
import com.iofast.android.extractor.*
import com.iofast.android.tree.TreeAccess
import com.iofast.android.tree.TreeText
import com.iofast.android.shareflow.AppShareLabels
import com.iofast.android.shareflow.NodeClicker
import com.iofast.android.shareflow.ShareFlowStateMachine

/**
 * Tracks foreground app snapshots and drives the Share->Copy-link flow
 * for social apps when the side key fires.
 *
 * Snapshot selection ignores our own package, SystemUI and keyboards so
 * the trampoline always reads the app that was in front just before the
 * side-key press. Opening any other app (or the home screen) clears it.
 *
 * Everything here runs on the main thread, so events are coalesced
 * ([DEBOUNCE_MS]) and each snapshot reads the address bar by view id,
 * falling back to a capped walk. Any error is logged, never thrown: an
 * exception or a stall here kills the process, and Samsung then turns
 * the accessibility toggle off.
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
            "com.google.android.inputmethod.latin",
            "com.samsung.android.honeyboard",
            "com.touchtype.swiftkey"
        )

        private val BROWSER_PACKAGES = setOf(
            "com.android.chrome",
            "com.sec.android.app.sbrowser"
        )

        /** Address-bar view ids, tried before any tree walk. */
        private val URL_BAR_IDS = mapOf(
            "com.android.chrome" to listOf("com.android.chrome:id/url_bar"),
            "com.sec.android.app.sbrowser" to listOf(
                "com.sec.android.app.sbrowser:id/location_bar_edit_text",
                "com.sec.android.app.sbrowser:id/url_bar"
            )
        )

        private const val DEBOUNCE_MS = 500L
        private const val SLOW_SNAPSHOT_MS = 150L

        private val RELEVANT_PACKAGES = setOf(
            "com.android.chrome",
            "com.sec.android.app.sbrowser",
            "com.google.android.youtube",
            "com.twitter.android",
            "com.x.android",
            "com.instagram.android"
        )

        /** Upper bound only; leaving the app clears the snapshot sooner. */
        private const val STALENESS_MS = 30 * 60_000L

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

        private fun clearSnapshot() {
            lastRelevantSnapshot = null
        }

        fun extractLastUrl(): String? {
            val snap = getBestSnapshot() ?: return null
            val extractor = extractors.firstOrNull { snap.packageName in it.packages }
                ?: return null
            return extractor.extract(snap.viewTreeText)
        }

        /** A share flow that never got going (no events from the app) is dropped after this. */
        private const val SHARE_FLOW_START_LIMIT_MS = 5_000L
        private var shareFlowStartedAt = 0L

        fun startShareFlow(packageName: String, previousClipboard: String?) {
            shareFlowStartedAt = System.currentTimeMillis()
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

    private val handler = Handler(Looper.getMainLooper())
    private var pendingPackage: String? = null
    private var lastLoggedUrl: String? = null
    private val snapshotRunnable = Runnable { takeSnapshotSafely() }

    override fun onServiceConnected() {
        super.onServiceConnected()
        ProblemLog.note("service", "connected")
    }

    override fun onUnbind(intent: Intent?): Boolean {
        ProblemLog.note("service", "unbound")
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        handler.removeCallbacks(snapshotRunnable)
        ProblemLog.note("service", "destroyed")
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        try {
            handleEvent(event ?: return)
        } catch (t: Throwable) {
            ProblemLog.note("service", "event failed: $t")
        }
    }

    private fun handleEvent(event: AccessibilityEvent) {
        val pkg = event.packageName?.toString() ?: return

        // If a share flow is active, feed events to the state machine;
        // the first event after the trampoline exits starts it.
        if (shareFlowActive && pkg == shareFlowPackage) {
            val sm = activeStateMachine
            if (sm != null) {
                sm.onWindowUpdate(rootInActiveWindow)
                return
            }
            if (System.currentTimeMillis() - shareFlowStartedAt <= SHARE_FLOW_START_LIMIT_MS) {
                ProblemLog.note("share", "starting share flow in $pkg")
                launchShareFlowStateMachine(pkg)
                return
            }
            ProblemLog.note("share", "share flow for $pkg never started; dropped")
            endShareFlow()
        }

        if (pkg in IGNORED_PACKAGES) return
        if (pkg !in RELEVANT_PACKAGES) {
            // Another app or the home screen came to the front: whatever we
            // saw before is no longer what the user is looking at.
            if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
                clearSnapshot()
                pendingPackage = null
                handler.removeCallbacks(snapshotRunnable)
            }
            return
        }

        when (event.eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> {
                // Social apps need no tree text: the share flow reads the
                // live window. Record them at once, no walk.
                if (pkg !in BROWSER_PACKAGES) {
                    if (lastRelevantSnapshot?.packageName != pkg) ProblemLog.note("service", "in front: $pkg")
                    recordSnapshot(Snapshot(pkg, ""))
                    return
                }
                // Coalesce bursts of content changes into one read.
                val alreadyQueued = pendingPackage == pkg
                pendingPackage = pkg
                if (!alreadyQueued) handler.postDelayed(snapshotRunnable, DEBOUNCE_MS)
            }
        }
    }

    private fun recordSnapshot(snap: Snapshot) {
        snapshotMap[snap.packageName] = snap
        lastRelevantSnapshot = snap
    }

    private fun takeSnapshotSafely() {
        val pkg = pendingPackage ?: return
        pendingPackage = null
        try {
            val root = rootInActiveWindow ?: return
            // By now the user may have switched apps (or opened iofast).
            if (root.packageName?.toString() != pkg) return

            val started = SystemClock.elapsedRealtime()
            recordSnapshot(Snapshot(pkg, browserText(pkg, root)))
            val ms = SystemClock.elapsedRealtime() - started

            val url = extractLastUrl()
            if (url != lastLoggedUrl || ms > SLOW_SNAPSHOT_MS) {
                ProblemLog.note("service", "read $pkg in ${ms}ms: ${url ?: "no url"}")
                lastLoggedUrl = url
            }
        } catch (t: Throwable) {
            ProblemLog.note("service", "snapshot failed for $pkg: $t")
        }
    }

    /** Address bar by id first; capped walk only when no id matches. */
    private fun browserText(pkg: String, root: AccessibilityNodeInfo): String {
        for (id in URL_BAR_IDS[pkg].orEmpty()) {
            val value = root.findAccessibilityNodeInfosByViewId(id)
                ?.firstNotNullOfOrNull { it.text?.toString()?.takeIf(String::isNotBlank) }
            if (value != null) return "${TreeText.URL_BAR_PREFIX}$value\n"
        }
        val result = TreeText.collect(root, NodeAccess)
        if (result.truncated) ProblemLog.note("service", "walk capped at ${result.nodesVisited} nodes in $pkg")
        return result.text
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
                ProblemLog.note("share", "copy-link clicked in $packageName")
                startActivity(intent)
                endShareFlow()
            },
            onFail = {
                ProblemLog.note("share", "share flow failed in $packageName (no Share or Copy link found in time)")
                CaptureNotifier.couldNotRead(this)
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
}

/** Adapts real accessibility nodes to the capped walk. */
private object NodeAccess : TreeAccess<AccessibilityNodeInfo> {
    override fun childCount(n: AccessibilityNodeInfo) = n.childCount
    override fun child(n: AccessibilityNodeInfo, i: Int): AccessibilityNodeInfo? = n.getChild(i)
    override fun text(n: AccessibilityNodeInfo): CharSequence? = n.text
    override fun description(n: AccessibilityNodeInfo): CharSequence? = n.contentDescription
    override fun viewId(n: AccessibilityNodeInfo): String? = n.viewIdResourceName
    override fun className(n: AccessibilityNodeInfo): CharSequence? = n.className
}
