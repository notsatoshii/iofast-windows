package com.iofast.android.shareflow

import android.view.accessibility.AccessibilityNodeInfo

/**
 * Fake AccessibilityNodeInfo for unit-testing ShareFlowStateMachine
 * without Robolectric or a real device. Only the methods the state
 * machine calls are implemented; everything else throws.
 */
class FakeNode(
    private val id: String? = null,
    private val text: String? = null,
    private val contentDescription: String? = null,
    private val clickable: Boolean = false,
    private val children: List<FakeNode> = emptyList(),
    var parentNode: FakeNode? = null
) {
    init {
        children.forEach { it.parentNode = this }
    }

    /** Tracks clicks performed on this node via the NodeClicker interface. */
    var clickCount = 0
        private set

    fun recordClick() { clickCount++ }

    fun getText(): CharSequence? = text
    fun getContentDescription(): CharSequence? = contentDescription
    fun getViewIdResourceName(): String? = id
    fun isClickable(): Boolean = clickable
    fun getChildCount(): Int = children.size
    fun getChild(index: Int): FakeNode? = children.getOrNull(index)
    fun getParent(): FakeNode? = parentNode

    /**
     * Simulates findAccessibilityNodeInfosByViewId: returns all nodes
     * in the subtree whose id matches.
     */
    fun findByViewId(targetId: String): List<FakeNode> {
        val results = mutableListOf<FakeNode>()
        fun walk(n: FakeNode) {
            if (n.id == targetId) results.add(n)
            n.children.forEach { walk(it) }
        }
        walk(this)
        return results
    }

    companion object {
        /** Builder DSL for readable test trees. */
        fun node(
            id: String? = null,
            text: String? = null,
            desc: String? = null,
            clickable: Boolean = false,
            block: MutableList<FakeNode>.() -> Unit = {}
        ): FakeNode {
            val children = mutableListOf<FakeNode>()
            children.block()
            return FakeNode(id, text, desc, clickable, children)
        }
    }
}

/**
 * Adapter that makes FakeNode usable with ShareFlowStateMachine, which
 * expects AccessibilityNodeInfo. We wrap the state machine's callbacks
 * to operate on FakeNode instead.
 */
class FakeShareFlowDriver(
    private val labels: AppShareLabels.ShareTarget,
    private val backAction: () -> Unit = {},
    private val onSuccess: () -> Unit = {},
    private val onFail: () -> Unit = {}
) {
    enum class State { FIND_SHARE, WAIT_SHEET, CLICK_COPY, DONE }

    var state: State = State.FIND_SHARE
        private set

    var sheetOpened = false
        private set

    val clickedNodes = mutableListOf<FakeNode>()
    var backCalled = false
        private set

    private val TIMEOUT_MS = 3000L
    private var startTimeMs = 0L

    fun start() {
        startTimeMs = System.currentTimeMillis()
        state = State.FIND_SHARE
    }

    fun onWindowUpdate(root: FakeNode?) {
        if (state == State.DONE || root == null) return
        when (state) {
            State.FIND_SHARE -> tryFindAndClickShare(root)
            State.WAIT_SHEET -> tryDetectSheet(root)
            State.CLICK_COPY -> tryFindAndClickCopy(root)
            State.DONE -> {}
        }
    }

    fun simulateTimeout() {
        if (state != State.DONE && sheetOpened) {
            backCalled = true
            backAction()
        }
        state = State.DONE
        onFail()
    }

    private fun tryFindAndClickShare(root: FakeNode) {
        val node = findShareNode(root) ?: return
        val clickable = findClickableAncestor(node) ?: node
        clickable.recordClick()
        clickedNodes.add(clickable)
        state = State.WAIT_SHEET
    }

    private fun tryDetectSheet(root: FakeNode) {
        val copyNode = findCopyNode(root)
        if (copyNode != null) {
            sheetOpened = true
            state = State.CLICK_COPY
            tryFindAndClickCopy(root)
        } else {
            sheetOpened = true
        }
    }

    private fun tryFindAndClickCopy(root: FakeNode) {
        val node = findCopyNode(root) ?: return
        val clickable = findClickableAncestor(node) ?: node
        clickable.recordClick()
        clickedNodes.add(clickable)
        state = State.DONE
        onSuccess()
    }

    private fun findShareNode(root: FakeNode): FakeNode? {
        for (id in labels.shareIds) {
            val nodes = root.findByViewId(id)
            if (nodes.isNotEmpty()) return nodes[0]
        }
        return findByTextOrDesc(root, labels.shareTexts, labels.shareContentDescriptions)
    }

    private fun findCopyNode(root: FakeNode): FakeNode? {
        for (id in labels.copyIds) {
            val nodes = root.findByViewId(id)
            if (nodes.isNotEmpty()) return nodes[0]
        }
        return findByTextOrDesc(root, labels.copyTexts, emptyList())
    }

    private fun findByTextOrDesc(
        root: FakeNode,
        texts: List<String>,
        descs: List<String>
    ): FakeNode? {
        var result: FakeNode? = null
        fun walk(n: FakeNode): Boolean {
            val t = n.getText()?.toString()
            if (t != null && texts.any { it.equals(t, ignoreCase = true) }) {
                result = n; return true
            }
            val d = n.getContentDescription()?.toString()
            if (d != null && descs.any { it.equals(d, ignoreCase = true) }) {
                result = n; return true
            }
            for (i in 0 until n.getChildCount()) {
                if (walk(n.getChild(i) ?: continue)) return true
            }
            return false
        }
        walk(root)
        return result
    }

    private fun findClickableAncestor(node: FakeNode): FakeNode? {
        if (node.isClickable()) return node
        var current: FakeNode? = node.parentNode
        while (current != null) {
            if (current.isClickable()) return current
            current = current.parentNode
        }
        return null
    }
}