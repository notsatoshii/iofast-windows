package com.iofast.android.extractor

import com.iofast.android.tree.TreeText

/**
 * Shared URL logic for Chrome and Samsung Internet.
 *
 * The service puts the address-bar text first, as "urlbar: <text>".
 * Browsers usually hide the scheme there ("example.com/post"), so a
 * domain-looking value gets "https://" added. Without that line, fall
 * back to the first http(s) URL anywhere in the tree text.
 */
object BrowserUrl {
    private val urlPattern = Regex("""https?://[^\s"'<>]+""", RegexOption.IGNORE_CASE)
    private val hostPattern = Regex("""^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?(/\S*)?$""", RegexOption.IGNORE_CASE)

    fun extract(viewTreeText: String): String? {
        fromUrlBar(viewTreeText)?.let { return it }
        return urlPattern.find(viewTreeText)?.value?.let(::tidy)
    }

    private fun fromUrlBar(text: String): String? {
        val line = text.lineSequence().firstOrNull { it.startsWith(TreeText.URL_BAR_PREFIX) } ?: return null
        val value = line.removePrefix(TreeText.URL_BAR_PREFIX).trim()
        if (value.isEmpty() || value.contains(' ')) return null
        if (urlPattern.matches(value)) return tidy(value)
        if (hostPattern.matches(value)) return tidy("https://$value")
        return null
    }

    private fun tidy(url: String) = url.trimEnd('/', ',', '.', ')')
}
