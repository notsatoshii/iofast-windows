package com.iofast.android.extractor

/**
 * Extracts URL from Samsung Internet's address bar.
 * Same strategy as Chrome: regex-match http(s) URLs in the view tree.
 */
class SamsungInternetExtractor : UrlExtractor {
    override val packages = setOf("com.sec.android.app.sbrowser")

    private val urlPattern = Regex(
        """https?://[^\s"'<>]+""",
        RegexOption.IGNORE_CASE
    )

    override fun extract(viewTreeText: String): String? {
        return urlPattern.find(viewTreeText)?.value?.trimEnd('/', ',', '.', ')')
    }
}