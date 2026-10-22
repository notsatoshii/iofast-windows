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
import com.iofast.android.data.AppDatabase
import com.iofast.android.data.CapturedLink
import com.iofast.android.shareflow.AppShareLabels
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * Side-key double-press lands here. For social apps, signals the
 * accessibility service to run the share-flow and exits immediately
 * so the social app regains focus (required for a11y clicks).
 * For browsers, reads the URL bar from the last snapshot directly.
 * If the a11y service is disabled, opens settings with an explanation.
 */
class CaptureTrampolineActivity : AppCompatActivity() {

    companion object {
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

        when (snap.packageName) {
            "com.google.android.youtube",
            "com.twitter.android", "com.x.android",
            "com.instagram.android" -> {
                // Save previous clipboard for restoration
                val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                val previousClip = cm.primaryClip?.getItemAt(0)?.text?.toString()

                // Signal the service and exit immediately so the social app
                // regains focus and the a11y service can click inside it
                UrlCaptureAccessibilityService.startShareFlow(
                    packageName = snap.packageName,
                    previousClipboard = previousClip
                )
                finish()
                return
            }

            "com.android.chrome",
            "com.sec.android.app.sbrowser" -> {
                val url = UrlCaptureAccessibilityService.extractLastUrl()
                if (url != null) {
                    kotlinx.coroutines.GlobalScope.launch(kotlinx.coroutines.Dispatchers.IO) {
                        AppDatabase.get(this@CaptureTrampolineActivity)
                            .capturedLinkDao()
                            .insert(CapturedLink(url = url, sourcePackage = snap.packageName))
                    }
                    Toast.makeText(this, getString(R.string.toast_captured, url), Toast.LENGTH_LONG).show()
                } else {
                    Toast.makeText(this, R.string.toast_fallback_share, Toast.LENGTH_LONG).show()
                }
                finish()
                return
            }

            else -> {
                Toast.makeText(this, R.string.toast_fallback_share, Toast.LENGTH_LONG).show()
                finish()
                return
            }
        }
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