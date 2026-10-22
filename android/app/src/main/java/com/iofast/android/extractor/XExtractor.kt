package com.iofast.android.extractor

/**
 * Extracts X (Twitter) post URL from view tree text.
 * X renders x.com or twitter.com URLs with status IDs.
 */
class XExtractor : UrlExtractor {
 override val packages = setOf("com.twitter.android", "com.x.android")

 private val urlPattern = Regex(
 """https?://(?:twitter|x)\.com/[^\s"'<>]+/status/\d+""",
 RegexOption.IGNORE_CASE
 )

 override fun extract(viewTreeText: String): String? {
 val match = urlPattern.find(viewTreeText) ?: return null
 // Normalize to x.com
 return match.value.replace("twitter.com", "x.com", ignoreCase = true)
 }
}