package com.iofast.android.shareflow

import org.junit.Assert.*
import org.junit.Test

/**
 * Tests the share-flow state machine logic using FakeShareFlowDriver,
 * which mirrors ShareFlowStateMachine's algorithm but operates on
 * FakeNode instead of AccessibilityNodeInfo. This proves the find/click
 * logic works without Robolectric or a real device.
 */
class ShareFlowStateMachineTest {

    private fun youtubeLabels() = AppShareLabels.YOUTUBE
    private fun xLabels() = AppShareLabels.X
    private fun instagramLabels() = AppShareLabels.INSTAGRAM

    // --- English labels ---

    @Test
    fun `finds and clicks share by English text then copy link`() {
        var successCalled = false
        val driver = FakeShareFlowDriver(
            labels = youtubeLabels(),
            onSuccess = { successCalled = true }
        )

        val root = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "Share", clickable = true))
            add(FakeNode.node(text = "Like"))
        }

        driver.start()
        driver.onWindowUpdate(root)
        // After clicking Share, state is WAIT_SHEET; no copy node yet
        assertEquals(FakeShareFlowDriver.State.WAIT_SHEET, driver.state)
        assertEquals(1, driver.clickedNodes.size)
        assertEquals("Share", driver.clickedNodes[0].getText())

        // Now sheet appears with Copy link
        val sheetRoot = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "Copy link", clickable = true))
            add(FakeNode.node(text = "Message"))
        }
        driver.onWindowUpdate(sheetRoot)

        assertTrue(successCalled)
        assertEquals(FakeShareFlowDriver.State.DONE, driver.state)
        assertEquals(2, driver.clickedNodes.size)
        assertEquals("Copy link", driver.clickedNodes[1].getText())
    }

    // --- Korean labels ---

    @Test
    fun `finds and clicks share by Korean text then copy link`() {
        var successCalled = false
        val driver = FakeShareFlowDriver(
            labels = xLabels(),
            onSuccess = { successCalled = true }
        )

        val root = FakeNode.node(clickable = true) {
            add(FakeNode.node(desc = "게시물 공유하기", clickable = true))
            add(FakeNode.node(text = "좋아요"))
        }

        driver.start()
        driver.onWindowUpdate(root)
        assertEquals(FakeShareFlowDriver.State.WAIT_SHEET, driver.state)
        assertEquals(1, driver.clickedNodes.size)

        val sheetRoot = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "링크 복사", clickable = true))
        }
        driver.onWindowUpdate(sheetRoot)

        assertTrue(successCalled)
        assertEquals(FakeShareFlowDriver.State.DONE, driver.state)
        assertEquals(2, driver.clickedNodes.size)
        assertEquals("링크 복사", driver.clickedNodes[1].getText())
    }

    // --- Non-clickable icon: climb to clickable parent ---

    @Test
    fun `climbs to clickable parent when share node is not clickable`() {
        var successCalled = false
        val driver = FakeShareFlowDriver(
            labels = instagramLabels(),
            onSuccess = { successCalled = true }
        )

        // Share icon is not clickable; its parent button is
        val parentButton = FakeNode.node(clickable = true) {
            add(FakeNode.node(desc = "Share", clickable = false))
        }
        val root = FakeNode.node(clickable = true) {
            add(parentButton)
        }

        driver.start()
        driver.onWindowUpdate(root)
        assertEquals(FakeShareFlowDriver.State.WAIT_SHEET, driver.state)
        // Should have clicked the parent, not the icon
        assertEquals(1, driver.clickedNodes.size)
        assertTrue(driver.clickedNodes[0].isClickable())
        // The parent should contain the share desc child
        assertEquals(1, driver.clickedNodes[0].getChildCount())

        val sheetRoot = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "Copy link", clickable = true))
        }
        driver.onWindowUpdate(sheetRoot)
        assertTrue(successCalled)
    }

    // --- Sheet never appears: timeout + one BACK ---

    @Test
    fun `timeout after share click sends one BACK and fails`() {
        var failCalled = false
        val driver = FakeShareFlowDriver(
            labels = youtubeLabels(),
            onFail = { failCalled = true }
        )

        val root = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "Share", clickable = true))
        }

        driver.start()
        driver.onWindowUpdate(root)
        assertEquals(FakeShareFlowDriver.State.WAIT_SHEET, driver.state)
        // tryDetectSheet was NOT called yet (no second window update),
        // so sheetOpened should still be false at this point.
        // But the real state machine sets sheetOpened=true in tryDetectSheet
        // which runs on the NEXT window event. Since we only sent one event,
        // sheetOpened is still false. simulateTimeout should NOT call BACK.
        assertFalse(driver.sheetOpened)

        // Simulate timeout (no copy-link ever appears, sheet never opened)
        driver.simulateTimeout()

        assertTrue(failCalled)
        assertFalse(driver.backCalled) // no BACK because sheet never opened
        assertEquals(FakeShareFlowDriver.State.DONE, driver.state)
    }

    @Test
    fun `timeout after sheet opened sends one BACK and fails`() {
        var failCalled = false
        val driver = FakeShareFlowDriver(
            labels = youtubeLabels(),
            onFail = { failCalled = true }
        )

        val root = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "Share", clickable = true))
        }

        driver.start()
        driver.onWindowUpdate(root)
        assertEquals(FakeShareFlowDriver.State.WAIT_SHEET, driver.state)

        // Second window update triggers tryDetectSheet which sets sheetOpened=true
        // but no copy node found, so stays in WAIT_SHEET
        val emptySheet = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "Something else"))
        }
        driver.onWindowUpdate(emptySheet)
        assertTrue(driver.sheetOpened)

        // Simulate timeout
        driver.simulateTimeout()

        assertTrue(failCalled)
        assertTrue(driver.backCalled) // BACK because sheet was opened
        assertEquals(FakeShareFlowDriver.State.DONE, driver.state)
    }

    // --- Copy missing in sheet: stays in WAIT_SHEET ---

    @Test
    fun `stays in WAIT_SHEET when copy button is missing from sheet`() {
        val driver = FakeShareFlowDriver(labels = xLabels())

        val root = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "Share", clickable = true))
        }
        driver.start()
        driver.onWindowUpdate(root)
        assertEquals(FakeShareFlowDriver.State.WAIT_SHEET, driver.state)

        // Sheet appears but has no copy option
        val sheetWithoutCopy = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "Message", clickable = true))
            add(FakeNode.node(text = "Email", clickable = true))
        }
        driver.onWindowUpdate(sheetWithoutCopy)

        // tryDetectSheet sets sheetOpened=true but doesn't find copy node,
        // so state stays in WAIT_SHEET (not CLICK_COPY)
        assertEquals(FakeShareFlowDriver.State.WAIT_SHEET, driver.state)
        assertTrue(driver.sheetOpened)
        assertEquals(1, driver.clickedNodes.size) // only share was clicked
    }

    // --- Resource id matching ---

    @Test
    fun `finds share by resource id when text does not match`() {
        var successCalled = false
        val driver = FakeShareFlowDriver(
            labels = youtubeLabels(),
            onSuccess = { successCalled = true }
        )

        // Node has the right id but wrong text (e.g., localized differently)
        val root = FakeNode.node(clickable = true) {
            add(FakeNode.node(
                id = "com.google.android.youtube:id/share_button",
                text = "Some other label",
                clickable = true
            ))
        }

        driver.start()
        driver.onWindowUpdate(root)
        assertEquals(FakeShareFlowDriver.State.WAIT_SHEET, driver.state)
        assertEquals(1, driver.clickedNodes.size)

        val sheetRoot = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "Copy link", clickable = true))
        }
        driver.onWindowUpdate(sheetRoot)
        assertTrue(successCalled)
    }

    // --- System chooser path (copy appears directly) ---

    @Test
    fun `handles system chooser where copy appears in same tree as share`() {
        var successCalled = false
        val driver = FakeShareFlowDriver(
            labels = instagramLabels(),
            onSuccess = { successCalled = true }
        )

        // System chooser shows both share targets and copy in one tree
        val root = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "Share", clickable = true))
            add(FakeNode.node(text = "Copy link", clickable = true))
        }

        driver.start()
        driver.onWindowUpdate(root)
        // FIND_SHARE finds and clicks Share first
        assertEquals(FakeShareFlowDriver.State.WAIT_SHEET, driver.state)

        // Next update sees Copy link in the tree -> transitions through
        // WAIT_SHEET -> CLICK_COPY -> DONE
        driver.onWindowUpdate(root)
        assertTrue(successCalled)
        assertEquals(FakeShareFlowDriver.State.DONE, driver.state)
    }

    // --- No share node found: stays in FIND_SHARE ---

    @Test
    fun `stays in FIND_SHARE when no share control exists`() {
        val driver = FakeShareFlowDriver(labels = youtubeLabels())

        val root = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "Like", clickable = true))
            add(FakeNode.node(text = "Subscribe", clickable = true))
        }

        driver.start()
        driver.onWindowUpdate(root)
        assertEquals(FakeShareFlowDriver.State.FIND_SHARE, driver.state)
        assertEquals(0, driver.clickedNodes.size)
    }

    // --- Content description matching for share ---

    @Test
    fun `finds share by contentDescription when text is null`() {
        var successCalled = false
        val driver = FakeShareFlowDriver(
            labels = xLabels(),
            onSuccess = { successCalled = true }
        )

        val root = FakeNode.node(clickable = true) {
            add(FakeNode.node(desc = "Share post", clickable = true))
        }

        driver.start()
        driver.onWindowUpdate(root)
        assertEquals(FakeShareFlowDriver.State.WAIT_SHEET, driver.state)
        assertEquals(1, driver.clickedNodes.size)

        val sheetRoot = FakeNode.node(clickable = true) {
            add(FakeNode.node(text = "복사", clickable = true))
        }
        driver.onWindowUpdate(sheetRoot)
        assertTrue(successCalled)
    }
}