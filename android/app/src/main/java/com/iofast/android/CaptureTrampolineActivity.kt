package com.iofast.android

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.iofast.android.data.AppDatabase
import com.iofast.android.data.CapturedLink
import kotlinx.coroutines.launch

/**
 * Side-key double-press lands here. Reads the last-captured URL from
 * the accessibility service, queues it, shows a toast, and exits.
 * No UI of its own — translucent, no history, excluded from recents.
 */
class CaptureTrampolineActivity : AppCompatActivity() {
 override fun onCreate(savedInstanceState: Bundle?) {
 super.onCreate(savedInstanceState)

 val url = UrlCaptureAccessibilityService.extractLastUrl()
 if (url != null) {
 lifecycleScope.launch {
 AppDatabase.get(this@CaptureTrampolineActivity)
 .capturedLinkDao()
 .insert(CapturedLink(url = url, sourcePackage = UrlCaptureAccessibilityService.lastSnapshot?.packageName))
 }
 Toast.makeText(this, getString(R.string.toast_captured, url), Toast.LENGTH_LONG).show()
 } else {
 Toast.makeText(this, R.string.toast_no_url, Toast.LENGTH_SHORT).show()
 }
 finish()
 }
}