package com.iofast.android.pairing

import java.net.URI
import java.net.URLDecoder

/**
 * The pairing link the PC shows: iofast://pair?key=<phone key>
 * Pure Kotlin (no android.net.Uri) so it is unit-tested on the JVM.
 * Only the key comes from the link; the inbox address stays the built-in
 * one, so a link from a web page cannot point the phone at another server.
 */
object PairLink {
    const val SCHEME = "iofast"
    const val HOST = "pair"

    private val KEY = Regex("^[A-Za-z0-9._~-]{16,256}$")

    fun isValidKey(key: String): Boolean = KEY.matches(key)

    /** Returns the key from a pairing link, or null if the link is not one. */
    fun parse(link: String?): String? {
        if (link.isNullOrBlank()) return null
        val uri = try { URI(link.trim()) } catch (_: Exception) { return null }
        if (!SCHEME.equals(uri.scheme, ignoreCase = true)) return null
        if (!HOST.equals(uri.host, ignoreCase = true)) return null
        val query = uri.rawQuery ?: return null
        val key = query.split('&').firstNotNullOfOrNull { part ->
            val i = part.indexOf('=')
            if (i > 0 && part.substring(0, i) == "key") decode(part.substring(i + 1)) else null
        } ?: return null
        return key.takeIf { isValidKey(it) }
    }

    /** What the user pasted on the home screen: a bare key or the whole pairing link. */
    fun fromPasted(text: String?): String? {
        val t = text?.trim().orEmpty()
        if (t.isEmpty()) return null
        if (t.startsWith("$SCHEME:", ignoreCase = true)) return parse(t)
        return t.takeIf { isValidKey(it) }
    }

    private fun decode(s: String): String? = try {
        URLDecoder.decode(s, "UTF-8")
    } catch (_: Exception) {
        null
    }
}
