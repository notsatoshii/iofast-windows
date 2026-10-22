package com.iofast.android.extractor

/**
 * Extracts Instagram post/reel URL from view tree text.
 * Instagram renders instagram.com/p/... or /reel/... in visible nodes.
 */
class InstagramExtractor : UrlExtractor {
 override val packages = setOf("com.instagram.android")

 private val urlPattern = Regex(
 """https?://(?:www\.)?instagram\.com/(?:p|reel)/[a-zA-Z0-9_-]+/?""",
 RegexOption.IGNORE_CASE
 )

 override fun extract(viewTreeText: String): String? {
 val match = urlPattern.find(viewTreeText) ?: return null
 return if (match.value.startsWith("http")) match.value else "https://${match.value}"
 }
}