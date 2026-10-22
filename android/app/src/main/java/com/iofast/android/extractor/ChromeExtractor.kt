package com.iofast.android.extractor

/**
 * Extracts URL from Chrome's address bar text.
 * Chrome renders the full URL in the url_bar node when focused,
 * or a display-URL when not; both are valid captures.
 */
class ChromeExtractor : UrlExtractor {
    override val packages = setOf("com.android.chrome")

    private val urlPattern = Regex(
        """https?://[^\s"'<>]+""",
        RegexOption.IGNORE_CASE
    )

    override fun extract(viewTreeText: String): String? {
        // Look for URL-bar marker first (id-based extraction happens in service)
        // Fallback: find any http(s) URL in the tree text
        return urlPattern.find(viewTreeText)?.value?.trimEnd('/', ',', '.', ')')
    }
}