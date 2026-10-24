package com.iofast.android.extractor

import com.iofast.android.ShareReceiverActivity
import com.iofast.android.tree.TreeText
import org.junit.Assert.*
import org.junit.Test

class BrowserUrlTest {

    @Test
    fun `url bar without scheme gets https`() {
        val text = "${TreeText.URL_BAR_PREFIX}example.com/blog/post-123"
        assertEquals("https://example.com/blog/post-123", BrowserUrl.extract(text))
    }

    @Test
    fun `url bar with scheme is kept`() {
        val text = "${TreeText.URL_BAR_PREFIX}http://localhost.test:8080/a?b=1"
        assertEquals("http://localhost.test:8080/a?b=1", BrowserUrl.extract(text))
    }

    @Test
    fun `search words in the url bar are not a link`() {
        assertNull(BrowserUrl.extract("${TreeText.URL_BAR_PREFIX}best pizza near me"))
    }

    @Test
    fun `falls back to the first link in the text`() {
        assertEquals("https://news.ycombinator.com/item?id=1",
            BrowserUrl.extract("Hacker News https://news.ycombinator.com/item?id=1. more"))
    }

    @Test
    fun `share text with words around the link`() {
        assertEquals("https://youtu.be/abc123",
            ShareReceiverActivity.firstUrl("Watch this! https://youtu.be/abc123 so good"))
        assertNull(ShareReceiverActivity.firstUrl("no link here"))
        assertNull(ShareReceiverActivity.firstUrl(null))
    }
}
