package com.iofast.android.tree

import org.junit.Assert.*
import org.junit.Test

class TreeTextTest {

    private class Node(
        val text: String? = null,
        val cls: String = "android.view.View",
        val id: String? = null,
        val kids: MutableList<Node> = mutableListOf()
    )

    private object Access : TreeAccess<Node> {
        override fun childCount(n: Node) = n.kids.size
        override fun child(n: Node, i: Int) = n.kids[i]
        override fun text(n: Node) = n.text
        override fun description(n: Node): CharSequence? = null
        override fun viewId(n: Node) = n.id
        override fun className(n: Node) = n.cls
    }

    @Test
    fun `keeps document order`() {
        val root = Node("a", kids = mutableListOf(
            Node("b", kids = mutableListOf(Node("c"))),
            Node("d")
        ))
        val r = TreeText.collect(root, Access)
        assertEquals("a b c d ", r.text)
        assertEquals(4, r.nodesVisited)
        assertFalse(r.truncated)
    }

    @Test
    fun `very deep tree stops at the depth cap without stack overflow`() {
        val root = Node("n0")
        var cur = root
        for (i in 1..20_000) {
            val next = Node("n$i")
            cur.kids.add(next)
            cur = next
        }
        val r = TreeText.collect(root, Access)
        assertTrue(r.truncated)
        assertEquals(TreeText.MAX_DEPTH + 1, r.nodesVisited)
    }

    @Test
    fun `very wide tree stops at the node cap`() {
        val root = Node("root")
        repeat(50_000) { root.kids.add(Node("x$it")) }
        val r = TreeText.collect(root, Access)
        assertTrue(r.truncated)
        assertEquals(TreeText.MAX_NODES, r.nodesVisited)
    }

    @Test
    fun `skips web page content but keeps the address bar`() {
        val page = Node(cls = "android.webkit.WebView", kids = mutableListOf(
            Node("https://inside-page.example/should-not-appear")
        ))
        val root = Node(kids = mutableListOf(
            Node("example.com/post", id = "com.android.chrome:id/url_bar"),
            page
        ))
        val r = TreeText.collect(root, Access)
        assertTrue(r.text.contains("example.com/post"))
        assertFalse(r.text.contains("inside-page"))
    }
}
