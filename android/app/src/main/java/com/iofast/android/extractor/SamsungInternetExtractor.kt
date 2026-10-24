package com.iofast.android.extractor

/**
 * Extracts URL from Samsung Internet's address bar.
 * Same strategy as Chrome: regex-match http(s) URLs in the view tree.
 */
class SamsungInternetExtractor : UrlExtractor {
    override val packages = setOf("com.sec.android.app.sbrowser")

    override fun extract(viewTreeText: String): String? = BrowserUrl.extract(viewTreeText)
}