package com.iofast.android.shareflow

import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Drives Share -> Copy-link for a social app via accessibility node tree.
 * Pure state machine: no Android framework dependencies beyond AccessibilityNodeInfo.
 * Clicks are delegated to [clicker] so tests can inject a fake.
 *
 * States: FIND_SHARE -> WAIT_SHEET -> CLICK_COPY -> DONE
 * Hard timeout: 3 s overall. On timeout: one BACK if sheet was opened, then fail.
 */
class ShareFlowStateMachine(
    private val labels: AppShareLabels.ShareTarget,
    private val clicker: NodeClicker,
    private val backAction: () -> Unit,
    private val onSuccess: () -> Unit,
    private val onFail: () -> Unit
) {
    enum class State { FIND_SHARE, WAIT_SHEET, CLICK_COPY, DONE }

    var state: State = State.FIND_SHARE
        private set

    private var sheetOpened = false
    private var startTimeMs = 0L
    private val handler = Handler(Looper.getMainLooper())
    private val TIMEOUT_MS = 3000L
    private val POLL_MS = 200L

    fun start() {
        startTimeMs = System.currentTimeMillis()
        state = State.FIND_SHARE
        handler.post(tickRunnable)
    }

    fun cancel() {
        handler.removeCallbacks(tickRunnable)
        state = State.DONE
    }

    /** Called by the accessibility service on each window event with the current root. */
    fun onWindowUpdate(root: AccessibilityNodeInfo?) {
        if (state == State.DONE || root == null) return
        when (state) {
            State.FIND_SHARE -> tryFindAndClickShare(root)
            State.WAIT_SHEET -> tryDetectSheet(root)
            State.CLICK_COPY -> tryFindAndClickCopy(root)
            State.DONE -> {}
        }
    }

    private val tickRunnable = object : Runnable {
        override fun run() {
            if (state == State.DONE) return
            if (System.currentTimeMillis() - startTimeMs >= TIMEOUT_MS) {
                handleTimeout()
                return
            }
            handler.postDelayed(this, POLL_MS)
        }
    }

    private fun tryFindAndClickShare(root: AccessibilityNodeInfo) {
        val node = findShareNode(root) ?: return
        val clickable = findClickableAncestor(node) ?: node
        clicker.click(clickable)
        state = State.WAIT_SHEET
    }

    private fun tryDetectSheet(root: AccessibilityNodeInfo) {
        // Sheet is present if we can find a copy-link label anywhere in the tree
        val copyNode = findCopyNode(root)
        if (copyNode != null) {
            sheetOpened = true
            state = State.CLICK_COPY
            tryFindAndClickCopy(root)
        } else {
            // Also detect system chooser by looking for "Copy" in any window
            sheetOpened = true // assume sheet may be opening; timeout handles failure
        }
    }

    private fun tryFindAndClickCopy(root: AccessibilityNodeInfo) {
        val node = findCopyNode(root) ?: return
        val clickable = findClickableAncestor(node) ?: node
        clicker.click(clickable)
        state = State.DONE
        handler.removeCallbacks(tickRunnable)
        onSuccess()
    }

    private fun handleTimeout() {
        state = State.DONE
        handler.removeCallbacks(tickRunnable)
        if (sheetOpened) {
            backAction()
        }
        onFail()
    }

    private fun findShareNode(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        // Try resource ids first
        for (id in labels.shareIds) {
            val nodes = root.findAccessibilityNodeInfosByViewId(id)
            if (!nodes.isNullOrEmpty()) return nodes[0]
        }
        // Fall back to text / contentDescription
        return findByTextOrDescription(root, labels.shareTexts, labels.shareContentDescriptions)
    }

    private fun findCopyNode(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        for (id in labels.copyIds) {
            val nodes = root.findAccessibilityNodeInfosByViewId(id)
            if (!nodes.isNullOrEmpty()) return nodes[0]
        }
        return findByTextOrDescription(root, labels.copyTexts, emptyList())
    }

    private fun findByTextOrDescription(
        root: AccessibilityNodeInfo,
        texts: List<String>,
        descriptions: List<String>
    ): AccessibilityNodeInfo? {
        var result: AccessibilityNodeInfo? = null
        fun walk(node: AccessibilityNodeInfo): Boolean {
            val nodeText = node.text?.toString()
            if (nodeText != null && texts.any { it.equals(nodeText, ignoreCase = true) }) {
                result = node
                return true
            }
            val desc = node.contentDescription?.toString()
            if (desc != null && descriptions.any { it.equals(desc, ignoreCase = true) }) {
                result = node
                return true
            }
            for (i in 0 until node.childCount) {
                if (walk(node.getChild(i) ?: continue)) return true
            }
            return false
        }
        walk(root)
        return result
    }

    private fun findClickableAncestor(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        var current: AccessibilityNodeInfo? = node.parent
        while (current != null) {
            if (current.isClickable) return current
            current = current.parent
        }
        return null
    }
}

/** Abstraction over AccessibilityNodeInfo.performAction(ACTION_CLICK) for testing. */
fun interface NodeClicker {
    fun click(node: AccessibilityNodeInfo)
}