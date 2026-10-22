package com.iofast.android.extractor

import org.junit.Assert.*
import org.junit.Test

/**
 * Tests extractors against realistic view-tree fixtures.
 * Social app fixtures are synthetic and do NOT contain URLs — those apps
 * don't render canonical URLs as visible text. Extractors return null
 * for these, confirming the share-flow clipboard path is required.
 * Browser fixtures DO contain URLs in the address bar node.
 */
class ExtractorTest {

    private fun loadFixture(name: String): String =
        javaClass.classLoader!!.getResourceAsStream("fixtures/$name")!!
            .bufferedReader().readText()

    // --- Browser extractors: URL bar route works ---

    @Test
    fun `chrome extractor finds URL in view tree`() {
        val text = loadFixture("chrome_viewtree.txt")
        val url = ChromeExtractor().extract(text)
        assertEquals("https://example.com/blog/post-123", url)
    }

    @Test
    fun `samsung internet extractor finds URL`() {
        val text = loadFixture("samsung_internet_viewtree.txt")
        val url = SamsungInternetExtractor().extract(text)
        assertEquals("https://news.ycombinator.com/item?id=38472916", url)
    }

    // --- Social app extractors: fallback only, no URL in real view trees ---

    @Test
    fun `youtube extractor returns null on realistic fixture without URL`() {
        val text = loadFixture("youtube_viewtree.txt")
        assertNull(YouTubeExtractor().extract(text))
    }

    @Test
    fun `x extractor returns null on realistic fixture without URL`() {
        val text = loadFixture("x_viewtree.txt")
        assertNull(XExtractor().extract(text))
    }

    @Test
    fun `instagram extractor returns null on realistic fixture without URL`() {
        val text = loadFixture("instagram_viewtree.txt")
        assertNull(InstagramExtractor().extract(text))
    }

    // --- Edge cases ---

    @Test
    fun `extractors return null on empty input`() {
        assertNull(ChromeExtractor().extract(""))
        assertNull(YouTubeExtractor().extract(""))
        assertNull(XExtractor().extract(""))
        assertNull(InstagramExtractor().extract(""))
        assertNull(SamsungInternetExtractor().extract(""))
    }

    @Test
    fun `extractors return null on unrelated text`() {
        val noise = "Just some random text with no URLs at all"
        assertNull(ChromeExtractor().extract(noise))
        assertNull(YouTubeExtractor().extract(noise))
        assertNull(XExtractor().extract(noise))
        assertNull(InstagramExtractor().extract(noise))
        assertNull(SamsungInternetExtractor().extract(noise))
    }

    // --- Social extractors still work when URL IS present (edge case / future) ---

    @Test
    fun `youtube extractor finds URL if somehow present in text`() {
        val text = "Some text https://www.youtube.com/watch?v=abc123 more text"
        assertEquals("https://www.youtube.com/watch?v=abc123", YouTubeExtractor().extract(text))
    }

    @Test
    fun `x extractor normalizes twitter to x when URL present`() {
        val text = "Check this https://twitter.com/user/status/1234567890 out"
        val url = XExtractor().extract(text)
        assertNotNull(url)
        assertTrue(url!!.startsWith("https://x.com/"))
    }

    @Test
    fun `instagram extractor finds reel URL if present`() {
        val text = "See https://www.instagram.com/reel/ABCdef123/ here"
        assertEquals("https://www.instagram.com/reel/ABCdef123/", InstagramExtractor().extract(text))
    }
}