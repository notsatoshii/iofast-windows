package com.iofast.android

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.iofast.android.data.AppDatabase
import com.iofast.android.data.CapturedLink
import kotlinx.coroutines.launch

/**
 * Receives ACTION_SEND text/plain or text/uri-list from any app's share sheet.
 * Extracts the first URL found in the shared text, queues it, toasts, exits.
 */
class ShareReceiverActivity : AppCompatActivity() {
 override fun onCreate(savedInstanceState: Bundle?) {
 super.onCreate(savedInstanceState)

 val url = intent.getStringExtra(Intent.EXTRA_TEXT)
 ?.trim()
 ?.takeIf { it.startsWith("http://") || it.startsWith("https://") }

 if (url != null) {
 lifecycleScope.launch {
 AppDatabase.get(this@ShareReceiverActivity)
 .capturedLinkDao()
 .insert(CapturedLink(url = url, sourcePackage = callingPackage))
 }
 Toast.makeText(this, getString(R.string.toast_captured, url), Toast.LENGTH_LONG).show()
 } else {
 Toast.makeText(this, R.string.toast_no_url, Toast.LENGTH_SHORT).show()
 }
 finish()
 }
}