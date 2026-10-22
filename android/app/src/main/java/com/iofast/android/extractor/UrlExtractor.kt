package com.iofast.android.extractor

/**
 * Stateless URL extractor for a specific app.
 * Implementations receive the package name and a snapshot of the
 * accessibility node tree text; they return the canonical URL or null.
 */
interface UrlExtractor {
    /** Package names this extractor handles (e.g. "com.android.chrome"). */
    val packages: Set<String>

    /** Extract a URL from the view-tree text snapshot, or null if not found. */
    fun extract(viewTreeText: String): String?
}