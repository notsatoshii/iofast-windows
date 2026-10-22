package com.iofast.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.iofast.android.data.AppDatabase
import com.iofast.android.data.CapturedLink
import com.iofast.android.extractor.ClipboardCleaner
import kotlinx.coroutines.launch

/**
 * Transparent activity launched after the share-flow clicks Copy-link.
 * Being focused allows clipboard read on Android 10+.
 * Reads clipboard, cleans URL, saves to Room, toasts, restores previous
 * clipboard content, and finishes immediately.
 */
class ClipboardReadActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_PACKAGE = "extra_package"
        const val EXTRA_PREVIOUS_CLIP = "extra_previous_clip"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val packageName = intent.getStringExtra(EXTRA_PACKAGE) ?: ""
        val previousClip = intent.getStringExtra(EXTRA_PREVIOUS_CLIP)

        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clipText = cm.primaryClip?.getItemAt(0)?.text?.toString()

        val url = if (clipText != null && clipText.startsWith("http")) {
            ClipboardCleaner.cleanForPackage(clipText, packageName)
        } else {
            null
        }

        if (url != null) {
            lifecycleScope.launch {
                AppDatabase.get(this@ClipboardReadActivity)
                    .capturedLinkDao()
                    .insert(CapturedLink(url = url, sourcePackage = packageName))
            }
            Toast.makeText(this, getString(R.string.toast_captured, url), Toast.LENGTH_LONG).show()
        } else {
            Toast.makeText(this, R.string.toast_fallback_share, Toast.LENGTH_LONG).show()
        }

        // Restore previous clipboard content
        if (previousClip != null) {
            cm.setPrimaryClip(ClipData.newPlainText("restored", previousClip))
        }

        finish()
    }
}