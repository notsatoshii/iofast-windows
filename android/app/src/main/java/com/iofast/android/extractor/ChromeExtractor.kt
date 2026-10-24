package com.iofast.android.extractor

/**
 * Extracts URL from Chrome's address bar text.
 * Chrome renders the full URL in the url_bar node when focused,
 * or a display-URL when not; both are valid captures.
 */
class ChromeExtractor : UrlExtractor {
    override val packages = setOf("com.android.chrome")

    override fun extract(viewTreeText: String): String? = BrowserUrl.extract(viewTreeText)
}