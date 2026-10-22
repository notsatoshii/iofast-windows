package com.iofast.android.extractor

/**
 * Extracts YouTube video/Short URL from view tree text.
 * YouTube renders the canonical URL or a recognizable slug
 * (youtube.com/watch?v=..., youtu.be/...) in visible nodes.
 */
class YouTubeExtractor : UrlExtractor {
 override val packages = setOf("com.google.android.youtube")

 private val urlPattern = Regex(
 """(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)([a-zA-Z0-9_-]+)""",
 RegexOption.IGNORE_CASE
 )

 override fun extract(viewTreeText: String): String? {
 val match = urlPattern.find(viewTreeText) ?: return null
 val fullUrl = match.value
 // Normalize to canonical form
 return if (fullUrl.startsWith("http")) fullUrl else "https://$fullUrl"
 }
}