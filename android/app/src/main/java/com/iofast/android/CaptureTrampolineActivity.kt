package com.iofast.android

import android.accessibilityservice.AccessibilityServiceInfo
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.iofast.android.data.AppDatabase
import com.iofast.android.data.CapturedLink
import com.iofast.android.extractor.ClipboardCleaner
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Side-key double-press lands here. For social apps (YouTube, X, Instagram),
 * drives the Share->Copy-link flow via the accessibility service, then reads
 * and cleans the clipboard. For browsers, reads the URL bar from the last
 * snapshot. Falls back to a toast directing the user to Share->iofast.
 *
 * If the accessibility service is disabled, opens settings with an explanation.
 */
class CaptureTrampolineActivity : AppCompatActivity() {

    companion object {
        private const val SHARE_FLOW_TIMEOUT_MS = 3000L
        private const val CLIPBOARD_POLL_MS = 200L
        private const val SERVICE_PACKAGE = "com.iofast.android"
        private const val SERVICE_CLASS = "com.iofast.android.UrlCaptureAccessibilityService"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (!isAccessibilityServiceEnabled()) {
            Toast.makeText(this, R.string.a11y_disabled_toast, Toast.LENGTH_LONG).show()
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            finish()
            return
        }

        val snap = UrlCaptureAccessibilityService.getBestSnapshot()
        if (snap == null) {
            Toast.makeText(this, R.string.toast_no_url, Toast.LENGTH_SHORT).show()
            finish()
            return
        }

        lifecycleScope.launch {
            val url = when (snap.packageName) {
                "com.google.android.youtube",
                "com.twitter.android", "com.x.android",
                "com.instagram.android" -> captureViaShareFlow(snap.packageName)

                "com.android.chrome",
                "com.sec.android.app.sbrowser" -> UrlCaptureAccessibilityService.extractLastUrl()

                else -> null
            }

            if (url != null) {
                AppDatabase.get(this@CaptureTrampolineActivity)
                    .capturedLinkDao()
                    .insert(CapturedLink(url = url, sourcePackage = snap.packageName))
                Toast.makeText(
                    this@CaptureTrampolineActivity,
                    getString(R.string.toast_captured, url),
                    Toast.LENGTH_LONG
                ).show()
            } else {
                Toast.makeText(
                    this@CaptureTrampolineActivity,
                    R.string.toast_fallback_share,
                    Toast.LENGTH_LONG
                ).show()
            }
            finish()
        }
    }

    /**
     * Drives Share->Copy-link via the accessibility service, polls the
     * clipboard for a matching URL, cleans it, and restores the previous
     * clipboard content. Bounded to SHARE_FLOW_TIMEOUT_MS.
     */
    private suspend fun captureViaShareFlow(packageName: String): String? {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val previousClip = cm.primaryClip?.getItemAt(0)?.text?.toString()

        // Signal the accessibility service to start the share flow
        UrlCaptureAccessibilityService.startShareFlow(packageName)

        val deadline = System.currentTimeMillis() + SHARE_FLOW_TIMEOUT_MS
        var result: String? = null

        while (System.currentTimeMillis() < deadline) {
            delay(CLIPBOARD_POLL_MS)
            val clipText = cm.primaryClip?.getItemAt(0)?.text?.toString() ?: continue
            if (clipText.startsWith("http://") || clipText.startsWith("https://")) {
                result = ClipboardCleaner.cleanForPackage(clipText, packageName)
                if (result != null) break
            }
        }

        UrlCaptureAccessibilityService.endShareFlow()

        // Restore previous clipboard content
        if (previousClip != null && result != null) {
            val clip = android.content.ClipData.newPlainText("restored", previousClip)
            cm.setPrimaryClip(clip)
        }

        return result
    }

    private fun isAccessibilityServiceEnabled(): Boolean {
        val am = getSystemService(Context.ACCESSIBILITY_SERVICE) as AccessibilityManager
        val enabledServices = am.getEnabledAccessibilityServiceList(
            AccessibilityServiceInfo.FEEDBACK_ALL_MASK
        )
        return enabledServices.any { info ->
            info.resolveInfo?.serviceInfo?.let {
                it.packageName == SERVICE_PACKAGE && it.name == SERVICE_CLASS
            } == true
        }
    }
}