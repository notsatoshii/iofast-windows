package com.iofast.android

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.iofast.android.data.AppDatabase
import com.iofast.android.data.CapturedLink
import com.iofast.android.diag.ProblemLog
import kotlinx.coroutines.launch

/**
 * Receives ACTION_SEND text/plain or text/uri-list from any app's share sheet.
 * Extracts the first URL found in the shared text, queues it, toasts, exits.
 */
class ShareReceiverActivity : AppCompatActivity() {

    companion object {
        private val URL = Regex("""https?://[^\s"'<>]+""", RegexOption.IGNORE_CASE)

        /** Apps often share "Look at this https://..." rather than a bare link. */
        fun firstUrl(text: String?): String? = text?.let { URL.find(it)?.value }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val url = firstUrl(intent.getStringExtra(Intent.EXTRA_TEXT))
        ProblemLog.note("share-in", "from ${callingPackage ?: "?"}: ${url ?: "no link in shared text"}")

        if (url != null) {
            val app = applicationContext
            val source = callingPackage
            IofastApp.appScope.launch {
                AppDatabase.get(app).capturedLinkDao()
                    .insert(CapturedLink(url = url, sourcePackage = source))
            }
            Toast.makeText(this, getString(R.string.toast_captured, url), Toast.LENGTH_LONG).show()
        } else {
            Toast.makeText(this, R.string.toast_no_url, Toast.LENGTH_SHORT).show()
        }
        finish()
    }
}
