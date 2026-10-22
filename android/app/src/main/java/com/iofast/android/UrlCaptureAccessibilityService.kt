package com.iofast.android

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.iofast.android.extractor.*

/**
 * Tracks the last foreground app and its view-tree text snapshot.
 * The trampoline reads this on launch to extract the URL that was
 * visible just before the side-key press brought our app forward.
 */
class UrlCaptureAccessibilityService : AccessibilityService() {

 data class Snapshot(
 val packageName: String,
 val viewTreeText: String,
 val timestamp: Long = System.currentTimeMillis()
 )

 companion object {
 @Volatile var lastSnapshot: Snapshot? = null
 private set

 private val extractors: List<UrlExtractor> = listOf(
 ChromeExtractor(),
 SamsungInternetExtractor(),
 YouTubeExtractor(),
 XExtractor(),
 InstagramExtractor()
 )

 fun extractLastUrl(): String? {
 val snap = lastSnapshot ?: return null
 val extractor = extractors.firstOrNull { snap.packageName in it.packages }
 ?: return null
 return extractor.extract(snap.viewTreeText)
 }
 }

 override fun onAccessibilityEvent(event: AccessibilityEvent?) {
 if (event == null) return
 when (event.eventType) {
 AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
 AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> {
 val pkg = event.packageName?.toString() ?: return
 val root = rootInActiveWindow ?: return
 val text = collectText(root)
 lastSnapshot = Snapshot(pkg, text)
 }
 }
 }

 override fun onInterrupt() {}

 private fun collectText(node: AccessibilityNodeInfo): String {
 val sb = StringBuilder()
 fun walk(n: AccessibilityNodeInfo) {
 n.text?.let { sb.append(it).append(' ') }
 for (i in 0 until n.childCount) {
 n.getChild(i)?.let { walk(it) }
 }
 }
 walk(node)
 return sb.toString()
 }
}