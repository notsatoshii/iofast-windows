package com.iofast.android.extractor

import org.junit.Assert.*
import org.junit.Test

class ClipboardCleanerTest {

    // YouTube tests

    @Test
    fun `youtube watch URL strips si param`() {
        val url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc123"
        assertEquals(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ClipboardCleaner.cleanYouTube(url)
        )
    }

    @Test
    fun `youtube shorts URL normalizes to watch format`() {
        val url = "https://www.youtube.com/shorts/AbCdEfGhIjK?si=xyz789"
        assertEquals(
            "https://www.youtube.com/watch?v=AbCdEfGhIjK",
            ClipboardCleaner.cleanYouTube(url)
        )
    }

    @Test
    fun `youtube youtu be short link normalizes`() {
        val url = "https://youtu.be/dQw4w9WgXcQ?si=tracking"
        assertEquals(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ClipboardCleaner.cleanYouTube(url)
        )
    }

    @Test
    fun `youtube clean URL passes through unchanged`() {
        val url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assertEquals(url, ClipboardCleaner.cleanYouTube(url))
    }

    @Test
    fun `youtube returns null for non-youtube URL`() {
        assertNull(ClipboardCleaner.cleanYouTube("https://example.com/video"))
    }

    // X tests

    @Test
    fun `x status URL strips s and t params`() {
        val url = "https://x.com/elonmusk/status/1234567890123456789?s=20&t=abcdef"
        assertEquals(
            "https://x.com/elonmusk/status/1234567890123456789",
            ClipboardCleaner.cleanX(url)
        )
    }

    @Test
    fun `x twitter dot com normalizes to x dot com`() {
        val url = "https://twitter.com/user/status/9876543210987654321?s=20"
        assertEquals(
            "https://x.com/user/status/9876543210987654321",
            ClipboardCleaner.cleanX(url)
        )
    }

    @Test
    fun `x clean URL passes through`() {
        val url = "https://x.com/user/status/1234567890"
        assertEquals(url, ClipboardCleaner.cleanX(url))
    }

    @Test
    fun `x returns null for non-status URL`() {
        assertNull(ClipboardCleaner.cleanX("https://x.com/elonmusk"))
    }

    // Instagram tests

    @Test
    fun `instagram post URL strips igsh param`() {
        val url = "https://www.instagram.com/p/ABC123def45/?igsh=MWd4aHk3bQ=="
        assertEquals(
            "https://www.instagram.com/p/ABC123def45/",
            ClipboardCleaner.cleanInstagram(url)
        )
    }

    @Test
    fun `instagram reel URL preserves reel segment`() {
        val url = "https://www.instagram.com/reel/XyZ789abc/?igsh=track"
        assertEquals(
            "https://www.instagram.com/reel/XyZ789abc/",
            ClipboardCleaner.cleanInstagram(url)
        )
    }

    @Test
    fun `instagram clean post URL passes through`() {
        val url = "https://www.instagram.com/p/ABC123def45/"
        assertEquals(url, ClipboardCleaner.cleanInstagram(url))
    }

    @Test
    fun `instagram returns null for profile URL`() {
        assertNull(ClipboardCleaner.cleanInstagram("https://www.instagram.com/natgeo/"))
    }

    // cleanForPackage dispatch

    @Test
    fun `cleanForPackage dispatches youtube`() {
        val url = "https://www.youtube.com/watch?v=test123&si=x"
        assertEquals(
            "https://www.youtube.com/watch?v=test123",
            ClipboardCleaner.cleanForPackage(url, "com.google.android.youtube")
        )
    }

    @Test
    fun `cleanForPackage dispatches x`() {
        val url = "https://twitter.com/u/status/111?s=20"
        assertEquals(
            "https://x.com/u/status/111",
            ClipboardCleaner.cleanForPackage(url, "com.twitter.android")
        )
    }

    @Test
    fun `cleanForPackage dispatches instagram`() {
        val url = "https://www.instagram.com/reel/Code/?igsh=x"
        assertEquals(
            "https://www.instagram.com/reel/Code/",
            ClipboardCleaner.cleanForPackage(url, "com.instagram.android")
        )
    }

    @Test
    fun `cleanForPackage returns null for unknown package`() {
        assertNull(ClipboardCleaner.cleanForPackage("https://example.com", "com.unknown.app"))
    }
}