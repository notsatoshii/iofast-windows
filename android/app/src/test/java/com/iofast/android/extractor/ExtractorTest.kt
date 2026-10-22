package com.iofast.android.extractor

import org.junit.Assert.*
import org.junit.Test

class ExtractorTest {

 private fun loadFixture(name: String): String =
 javaClass.classLoader!!.getResourceAsStream("fixtures/$name")!!
 .bufferedReader().readText()

 @Test
 fun `chrome extractor finds URL in view tree`() {
 val text = loadFixture("chrome_viewtree.txt")
 val url = ChromeExtractor().extract(text)
 assertEquals("https://example.com/blog/post-123", url)
 }

 @Test
 fun `youtube extractor finds watch URL`() {
 val text = loadFixture("youtube_viewtree.txt")
 val url = YouTubeExtractor().extract(text)
 assertEquals("https://www.youtube.com/watch?v=dQw4w9WgXcQ", url)
 }

 @Test
 fun `x extractor normalizes to x dot com`() {
 val text = loadFixture("x_viewtree.txt")
 val url = XExtractor().extract(text)
 assertNotNull(url)
 assertTrue(url!!.startsWith("https://x.com/"))
 assertTrue(url.contains("/status/"))
 }

 @Test
 fun `instagram extractor finds post URL`() {
 val text = loadFixture("instagram_viewtree.txt")
 val url = InstagramExtractor().extract(text)
 assertEquals("https://www.instagram.com/p/ABC123def45/", url)
 }

 @Test
 fun `samsung internet extractor finds URL`() {
 val text = loadFixture("samsung_internet_viewtree.txt")
 val url = SamsungInternetExtractor().extract(text)
 assertEquals("https://news.ycombinator.com/item?id=38472916", url)
 }

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
}