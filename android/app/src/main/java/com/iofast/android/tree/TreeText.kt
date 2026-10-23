package com.iofast.android.tree

/**
 * Read-only view of a node tree. Lets the capped walk run against real
 * AccessibilityNodeInfo on the phone and plain fakes in JVM tests.
 */
interface TreeAccess<N> {
    fun childCount(n: N): Int
    fun child(n: N, i: Int): N?
    fun text(n: N): CharSequence?
    fun description(n: N): CharSequence?
    fun viewId(n: N): String?
    fun className(n: N): CharSequence?
}

/**
 * Flattens a node tree to text with hard limits.
 *
 * The old walk was recursive, unbounded and ran on the main thread for
 * every content change. A long web page in Chrome exposes thousands of
 * nodes (each one a binder call), which stalls the service until the
 * system kills it and Samsung switches the accessibility toggle off.
 * This walk is iterative, stops at [maxNodes] / [maxDepth], and skips
 * web-content subtrees, where the address bar never lives.
 */
object TreeText {
    const val MAX_NODES = 1500
    const val MAX_DEPTH = 60
    const val URL_BAR_PREFIX = "urlbar: "

    private val WEB_CONTENT_CLASSES = setOf("android.webkit.WebView")

    data class Result(val text: String, val nodesVisited: Int, val truncated: Boolean)

    fun <N> collect(
        root: N,
        access: TreeAccess<N>,
        maxNodes: Int = MAX_NODES,
        maxDepth: Int = MAX_DEPTH
    ): Result {
        val sb = StringBuilder()
        val stack = ArrayDeque<Pair<N, Int>>()
        stack.addLast(root to 0)
        var visited = 0
        var truncated = false

        while (stack.isNotEmpty()) {
            if (visited >= maxNodes) {
                truncated = true
                break
            }
            val (node, depth) = stack.removeLast()
            visited++

            access.text(node)?.let { sb.append(it).append(' ') }
            access.description(node)?.let { sb.append(it).append(' ') }
            access.viewId(node)?.let { sb.append('[').append(it).append("] ") }

            if (access.className(node)?.toString() in WEB_CONTENT_CLASSES) continue
            if (depth >= maxDepth) {
                if (access.childCount(node) > 0) truncated = true
                continue
            }
            // Push children in reverse so they pop in document order.
            for (i in access.childCount(node) - 1 downTo 0) {
                access.child(node, i)?.let { stack.addLast(it to depth + 1) }
            }
        }
        return Result(sb.toString(), visited, truncated)
    }
}
