package com.iofast.android.extractor

import android.net.Uri

/**
 * Validates and cleans URLs read from the clipboard after the
 * Share->Copy-link flow. Strips tracking params per app and
 * normalizes to canonical form.
 */
object ClipboardCleaner {

    private val YOUTUBE_PATTERN = Regex(
        """https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)([a-zA-Z0-9_-]+)""",
        RegexOption.IGNORE_CASE
    )

    private val X_PATTERN = Regex(
        """https?://(?:twitter|x)\.com/([a-zA-Z0-9_]+)/status/(\d+)""",
        RegexOption.IGNORE_CASE
    )

    private val INSTAGRAM_PATTERN = Regex(
        """https?://(?:www\.)?instagram\.com/(?:p|reel)/([a-zA-Z0-9_-]+)""",
        RegexOption.IGNORE_CASE
    )

    /** Tracking params to strip per host pattern. */
    private val STRIP_PARAMS = setOf(
        "si", "igsh", "utm_source", "utm_medium", "utm_campaign",
        "utm_content", "utm_term", "s", "t", "feature", "ref"
    )

    fun cleanForPackage(url: String, packageName: String): String? {
        return when (packageName) {
            "com.google.android.youtube" -> cleanYouTube(url)
            "com.twitter.android", "com.x.android" -> cleanX(url)
            "com.instagram.android" -> cleanInstagram(url)
            else -> null
        }
    }

    fun cleanYouTube(url: String): String? {
        val match = YOUTUBE_PATTERN.find(url) ?: return null
        val videoId = match.groups[1]?.value ?: return null
        // Always normalize to youtube.com/watch?v= for consistency
        return "https://www.youtube.com/watch?v=$videoId"
    }

    fun cleanX(url: String): String? {
        val match = X_PATTERN.find(url) ?: return null
        val user = match.groups[1]?.value ?: return null
        val statusId = match.groups[2]?.value ?: return null
        return "https://x.com/$user/status/$statusId"
    }

    fun cleanInstagram(url: String): String? {
        val match = INSTAGRAM_PATTERN.find(url) ?: return null
        val code = match.groups[1]?.value ?: return null
        // Determine if reel or post from original URL
        val isReel = url.contains("/reel/", ignoreCase = true)
        val segment = if (isReel) "reel" else "p"
        return "https://www.instagram.com/$segment/$code/"
    }
}